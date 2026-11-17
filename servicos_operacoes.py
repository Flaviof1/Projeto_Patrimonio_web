import itens_sem_plaqueta
import json
import os
import pandas as pd
import shutil
import tempfile
import zipfile
from datetime import (
    datetime,
)
from itens_sem_plaqueta import (
    carregar_itens_sem_plaqueta,
)
from mysql_store import (
    MySQLStore,
    mysql_ativo,
)
from pathlib import (
    Path,
    PurePosixPath,
)
from paths import (
    ARQUIVO_CAMPOS,
    ARQUIVO_RESULTADO,
    BACKUPS_DIR,
    DADOS_TRABALHO_DIR,
    DATA_DIR,
    criar_backup_circular,
    garantir_pastas,
)
from preparar_dados import (
    carregar_bens_alienados,
    carregar_csv_entrada,
    validar_colunas,
)
from servicos_patrimonio import (
    _arquivo_ativo,
)

MAX_BACKUP_BYTES = 1024 * 1024 * 1024

NOMES_DADOS_BACKUP = ('dados_entrada', 'dados_trabalho', 'relatorios')

def _criar_backup_web():
    garantir_pastas()
    nome = f'backup_web_{datetime.now():%Y%m%d_%H%M%S_%f}.zip'
    destino = BACKUPS_DIR / nome
    temporario = destino.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(
            temporario, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as arquivo_zip:
            manifesto = {
                'formato': 'gestao-patrimonial-web',
                'versao': 1,
                'criado_em': datetime.now().astimezone().isoformat(timespec='seconds'),
            }
            arquivo_zip.writestr(
                'manifesto.json',
                json.dumps(manifesto, ensure_ascii=False, indent=2),
            )
            for nome_pasta in NOMES_DADOS_BACKUP:
                pasta = DATA_DIR / nome_pasta
                if not pasta.exists():
                    continue
                for arquivo in sorted(pasta.rglob('*')):
                    if arquivo.is_file():
                        arquivo_zip.write(
                            arquivo, arquivo.relative_to(DATA_DIR).as_posix()
                        )
        os.replace(temporario, destino)
    except OSError:
        temporario.unlink(missing_ok=True)
        raise
    return destino

def _validar_backup(caminho_zip, extracao):
    try:
        with zipfile.ZipFile(caminho_zip) as arquivo_zip:
            membros = arquivo_zip.infolist()
            total_descompactado = sum(membro.file_size for membro in membros)
            if total_descompactado > MAX_BACKUP_BYTES:
                raise ValueError('O backup excede o limite de tamanho permitido.')

            nomes = set()
            for membro in membros:
                nome = membro.filename
                caminho = PurePosixPath(nome)
                modo_arquivo = (membro.external_attr >> 16) & 0o170000
                if (
                    not nome
                    or '\\' in nome
                    or caminho.is_absolute()
                    or '..' in caminho.parts
                    or not caminho.parts
                    or caminho.parts[0] not in (*NOMES_DADOS_BACKUP, 'manifesto.json')
                    or modo_arquivo == 0o120000
                ):
                    raise ValueError('O backup contém um caminho inválido.')
                if nome in nomes:
                    raise ValueError('O backup contém entradas duplicadas.')
                nomes.add(nome)

            if 'manifesto.json' not in nomes:
                raise ValueError('O arquivo não é um backup da versão web.')
            manifesto = json.loads(arquivo_zip.read('manifesto.json'))
            if (
                not isinstance(manifesto, dict)
                or
                manifesto.get('formato') != 'gestao-patrimonial-web'
                or manifesto.get('versao') != 1
            ):
                raise ValueError('Formato de backup não compatível.')

            arquivo_zip.extractall(extracao)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise ValueError(f'Não foi possível validar o backup: {error}') from error

    pasta_entrada = extracao / 'dados_entrada'
    arquivo_csv = pasta_entrada / 'Localizar_Bens.csv'
    if arquivo_csv.is_file():
        validar_colunas(carregar_csv_entrada(str(arquivo_csv)))

    arquivo_alienados = pasta_entrada / 'Bens_Alienados.xlsx'
    if arquivo_alienados.is_file():
        carregar_bens_alienados(str(arquivo_alienados))

    arquivo_ods = pasta_entrada / itens_sem_plaqueta.ARQUIVO_ORIGEM
    if arquivo_ods.is_file():
        itens = carregar_itens_sem_plaqueta(str(arquivo_ods))
        if not {'descricao', 'setor atual'}.issubset(itens.columns):
            raise ValueError('A planilha ODS do backup não possui cabeçalhos válidos.')

    for nome in ('conferencia_resultado.xlsx', 'conferencia_campo.xlsx'):
        planilha = extracao / 'dados_trabalho' / nome
        if planilha.is_file():
            try:
                dados = pd.read_excel(planilha)
            except (OSError, ValueError, ImportError) as error:
                raise ValueError(f'A planilha {nome} no backup está inválida.') from error
            if 'Setor Atual' not in dados.columns:
                raise ValueError(f'A planilha {nome} no backup não contém "Setor Atual".')

def _salvar_planilha(dados):
    garantir_pastas()
    criar_backup_circular(forcar=True)
    destinos = (Path(ARQUIVO_RESULTADO), Path(ARQUIVO_CAMPOS))
    with tempfile.TemporaryDirectory(
        prefix='salvamento_', dir=DADOS_TRABALHO_DIR
    ) as pasta_temp:
        pasta_temp = Path(pasta_temp)
        staged = []
        for destino in destinos:
            temporario = pasta_temp / destino.name
            dados.to_excel(temporario, index=False)
            staged.append((temporario, destino))

        if mysql_ativo():
            MySQLStore.from_environment().replace_all(dados)

        antigos = []
        instalados = []
        try:
            for temporario, destino in staged:
                antigo = pasta_temp / f'antigo_{destino.name}'
                if destino.exists():
                    os.replace(destino, antigo)
                    antigos.append((antigo, destino))
                os.replace(temporario, destino)
                instalados.append(destino)
        except Exception:
            for destino in reversed(instalados):
                destino.unlink(missing_ok=True)
            for antigo, destino in reversed(antigos):
                if destino.exists():
                    destino.unlink()
                os.replace(antigo, destino)
            raise
    from paths import criar_versao_resultado
    criar_versao_resultado()

def _sincronizar_mysql_com_planilha():
    if not mysql_ativo():
        return
    caminho = _arquivo_ativo()
    dados = (
        pd.read_excel(caminho).fillna('')
        if caminho is not None
        else pd.DataFrame()
    )
    MySQLStore.from_environment().replace_all(dados)

def _restaurar_backup(nome):
    backup = BACKUPS_DIR / nome
    if backup.parent != BACKUPS_DIR or not backup.is_file() or backup.suffix != '.zip':
        raise ValueError('Backup não encontrado.')

    with tempfile.TemporaryDirectory(prefix='restauracao_', dir=DATA_DIR) as pasta_temp:
        temporario = Path(pasta_temp)
        extracao = temporario / 'dados_restaurados'
        extracao.mkdir()
        _validar_backup(backup, extracao)
        _criar_backup_web()

        substituidos = []
        try:
            for nome_pasta in NOMES_DADOS_BACKUP:
                atual = DATA_DIR / nome_pasta
                restaurado = extracao / nome_pasta
                antigo = temporario / f'antigo_{nome_pasta}'
                if atual.exists():
                    os.replace(atual, antigo)
                else:
                    antigo = None
                substituidos.append((atual, antigo))
                if restaurado.exists():
                    os.replace(restaurado, atual)
                else:
                    atual.mkdir(parents=True)
        except OSError:
            for atual, antigo in reversed(substituidos):
                if atual.exists():
                    shutil.rmtree(atual)
                if antigo is not None and antigo.exists():
                    os.replace(antigo, atual)
            raise
