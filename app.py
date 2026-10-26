import json
import getpass
import difflib
import os
import re
import socket
import shutil
import tempfile
import zipfile
import unicodedata
from functools import lru_cache, wraps
from io import BytesIO
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from secrets import token_hex, token_urlsafe

import pandas as pd
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
)
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

import gerar_relatorios
import itens_sem_plaqueta
import utils
from busca_web import aplicar_filtros_busca
from constantes import ESTADO_OPCOES, STATUS_OPCOES
from dados_patrimoniais import enriquecer_com_dados_patrimoniais
from itens_sem_plaqueta import carregar_itens_sem_plaqueta
from paths import (
    BACKUPS_DIR,
    DATA_DIR,
    DADOS_ENTRADA_DIR,
    DADOS_TRABALHO_DIR,
    FOTOS_DIR,
    RELATORIOS_DIR,
    ARQUIVO_CAMPOS,
    ARQUIVO_RESULTADO,
    criar_backup_circular,
    garantir_pastas,
    ler_timestamp_preparacao,
    salvar_timestamp_preparacao,
)
from mysql_store import MySQLStore, mysql_ativo
from preparar_dados import (
    ARQUIVO_BAIXADOS,
    carregar_bens_alienados,
    carregar_bens_baixados,
    carregar_csv_entrada,
    criar_registros_sem_plaqueta,
    formatar_plaqueta,
    formatar_plaqueta_exibicao,
    importar_csv_entrada,
    marcar_duplicidades,
    preparar_sistema_refinado,
    validar_colunas,
)

NOMES_DADOS_BACKUP = ('dados_entrada', 'dados_trabalho', 'relatorios')
EXTENSOES_IMAGEM = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
MAX_BACKUP_BYTES = 1024 * 1024 * 1024
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_PHOTO_FILE_BYTES = 40 * 1024 * 1024
PHOTO_MIME_TYPES = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'BMP': 'image/bmp',
    'GIF': 'image/gif',
    'WEBP': 'image/webp',
}


def _normalizar_valor(valor):
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(valor, 'item'):
        valor = valor.item()
    if isinstance(valor, (str, int, float, bool)):
        return valor
    return str(valor)


def _registros_json(df):
    registros = []
    for indice, linha in df.iterrows():
        registro = {'id': int(indice)}
        registro.update({
            str(coluna): _normalizar_valor(valor)
            for coluna, valor in linha.items()
        })
        registros.append(registro)
    return registros


def _pagina_requisitada(total):
    try:
        pagina = int(request.args.get('pagina', '1'))
        por_pagina = int(request.args.get('por_pagina', '50'))
    except ValueError:
        abort(400, description='A página e a quantidade de itens devem ser números inteiros.')
    if pagina < 1 or por_pagina < 1 or por_pagina > 100:
        abort(400, description='Página inválida. São permitidos de 1 a 100 itens por página.')
    pagina = min(pagina, max(1, (total + por_pagina - 1) // por_pagina))
    inicio = (pagina - 1) * por_pagina
    return pagina, por_pagina, inicio, min(inicio + por_pagina, total)


def _arquivo_leilao():
    return DADOS_TRABALHO_DIR / 'itens_leilao.json'


def _carregar_itens_leilao():
    caminho = _arquivo_leilao()
    if not caminho.exists():
        return []
    try:
        dados = json.loads(caminho.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError('Não foi possível ler a lista de itens para leilão.') from error
    if not isinstance(dados, list) or any(not isinstance(item, dict) for item in dados):
        raise ValueError('A lista de itens para leilão está em formato inválido.')
    return dados


def _salvar_itens_leilao(itens):
    caminho = _arquivo_leilao()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix('.json.tmp')
    try:
        temporario.write_text(
            json.dumps(itens, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        os.replace(temporario, caminho)
    except OSError:
        temporario.unlink(missing_ok=True)
        raise


def _normalizar_cabecalho_leilao(valor):
    texto = unicodedata.normalize('NFKD', str(valor))
    texto = ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r'[^a-z0-9]+', ' ', texto.lower()).strip()


def _ler_arquivo_leilao(arquivo):
    nome = secure_filename(arquivo.filename or '')
    extensao = Path(nome).suffix.lower()
    if extensao not in {'.xlsx', '.csv'}:
        raise ValueError('Envie a lista em formato Excel (.xlsx) ou CSV (.csv).')
    conteudo = arquivo.read()
    if not conteudo:
        raise ValueError('O arquivo da lista está vazio.')
    if len(conteudo) > 20 * 1024 * 1024:
        raise ValueError('O arquivo da lista excede o limite de 20 MB.')
    try:
        buffer = BytesIO(conteudo)
        if extensao == '.xlsx':
            planilha = pd.read_excel(buffer, dtype=object)
        else:
            try:
                planilha = pd.read_csv(
                    buffer, sep=None, engine='python', dtype=object, encoding='utf-8-sig'
                )
            except UnicodeDecodeError:
                buffer.seek(0)
                planilha = pd.read_csv(
                    buffer, sep=None, engine='python', dtype=object, encoding='latin1'
                )
    except (OSError, ValueError, ImportError, zipfile.BadZipFile, pd.errors.ParserError) as error:
        raise ValueError(f'Não foi possível ler a lista enviada: {error}') from error
    if planilha.empty:
        raise ValueError('A lista enviada não contém itens.')

    aliases = {
        'plaqueta': {'plaqueta', 'numero plaqueta', 'n plaqueta', 'tombamento'},
        'descricao': {
            'descricao', 'bem descricao', 'descricao bem', 'bem', 'nome bem',
            'item', 'descricao do bem', 'bem descricao item',
        },
        'setor': {'setor', 'setor atual', 'local', 'localizacao', 'unidade'},
        'data': {'data', 'data aquisicao', 'data de aquisicao', 'data cadastro'},
    }
    colunas = {}
    for coluna in planilha.columns:
        normalizado = _normalizar_cabecalho_leilao(coluna)
        for destino, nomes in aliases.items():
            if normalizado in nomes:
                colunas.setdefault(destino, coluna)
    if 'descricao' not in colunas:
        raise ValueError(
            'A planilha precisa ter uma coluna de descrição (ex.: Descrição, Bem ou Item).'
        )

    def valor_celula(linha, campo):
        if campo not in colunas:
            return ''
        valor = linha.get(colunas[campo], '')
        if pd.isna(valor):
            return ''
        if isinstance(valor, (date, datetime, pd.Timestamp)):
            return valor.strftime('%Y-%m-%d')
        return str(valor).strip()

    itens = []
    for _, linha in planilha.iterrows():
        descricao = valor_celula(linha, 'descricao')
        if not descricao:
            continue
        itens.append({
            'id': token_hex(12),
            'plaqueta_informada': valor_celula(linha, 'plaqueta')[:100],
            'descricao': descricao[:500],
            'setor': valor_celula(linha, 'setor')[:255],
            'data': valor_celula(linha, 'data')[:50],
            'situacao': 'PENDENTE_IDENTIFICACAO',
            'plaqueta_vinculada': '',
        })
    if not itens:
        raise ValueError('A lista não contém linhas com descrição de item.')
    return itens


def _normalizar_texto_correspondencia(valor):
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    texto = ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return ' '.join(re.findall(r'[a-z0-9]+', texto.lower()))


def _sugerir_correspondencias(item, dados):
    if dados is None or 'Plaqueta' not in dados.columns or 'Descrição' not in dados.columns:
        return []
    descricao = _normalizar_texto_correspondencia(item.get('descricao'))
    plaqueta_informada = str(item.get('plaqueta_informada', '')).strip()
    setor_informado = _normalizar_texto_correspondencia(item.get('setor'))
    candidatos = []
    for _, bem in dados.iterrows():
        plaqueta = formatar_plaqueta_exibicao(bem.get('Plaqueta', ''))
        if not plaqueta:
            continue
        descricao_base = _normalizar_texto_correspondencia(bem.get('Descrição', ''))
        similaridade = difflib.SequenceMatcher(None, descricao, descricao_base).ratio()
        if descricao and descricao in descricao_base:
            similaridade = max(similaridade, 0.85)
        if plaqueta_informada and plaqueta_informada == plaqueta:
            similaridade = 1.0
        setor_base = _normalizar_texto_correspondencia(bem.get('Setor Atual', ''))
        if setor_informado and setor_informado == setor_base:
            similaridade = min(1.0, similaridade + 0.1)
        if similaridade < 0.35:
            continue
        candidatos.append({
            'plaqueta': plaqueta,
            'descricao': str(bem.get('Descrição', '') or ''),
            'setor': str(bem.get('Setor Atual', '') or ''),
            'similaridade': round(similaridade * 100),
        })
    candidatos.sort(key=lambda candidato: candidato['similaridade'], reverse=True)
    return candidatos[:5]


@lru_cache(maxsize=256)
def _miniatura_foto(nome, modificado_ns, tamanho):
    caminho = FOTOS_DIR / nome
    with Image.open(caminho) as imagem:
        imagem = ImageOps.exif_transpose(imagem)
        imagem.thumbnail((96, 72), Image.Resampling.LANCZOS)
        if 'A' in imagem.getbands():
            formato = 'PNG'
            mime_type = 'image/png'
            saida = BytesIO()
            imagem.save(saida, format=formato, optimize=True)
        else:
            formato = 'JPEG'
            mime_type = 'image/jpeg'
            saida = BytesIO()
            imagem.convert('RGB').save(saida, format=formato, quality=72, optimize=True)
    return saida.getvalue(), mime_type


def _arquivo_ativo():
    resultado = Path(ARQUIVO_RESULTADO)
    trabalho = Path(ARQUIVO_CAMPOS)
    if resultado.is_file():
        return resultado
    if trabalho.is_file():
        return trabalho
    return None


def _carregar_base():
    if mysql_ativo():
        dados = MySQLStore.from_environment().load()
        if dados is None:
            return None
        if 'Setor Atual' not in dados.columns:
            raise ValueError('A base MySQL não contém a coluna "Setor Atual".')
        if 'Foto' not in dados.columns:
            dados['Foto'] = ''
        return marcar_duplicidades(formatar_plaqueta(dados))

    caminho = _arquivo_ativo()
    if caminho is None:
        return None
    try:
        dados = pd.read_excel(caminho).fillna('')
    except (OSError, ValueError, ImportError) as error:
        raise ValueError(f'Não foi possível ler a base {caminho.name}: {error}') from error
    if 'Setor Atual' not in dados.columns:
        raise ValueError('A planilha de conferência não contém a coluna "Setor Atual".')
    if 'Foto' not in dados.columns:
        dados['Foto'] = ''
    return marcar_duplicidades(formatar_plaqueta(dados))


def _setores(dados):
    if dados is None or 'Setor Atual' not in dados.columns:
        return []
    return sorted({
        str(valor).strip()
        for valor in dados['Setor Atual'].dropna()
        if str(valor).strip()
    }, key=str.casefold)


def _aplicar_filtros_busca(dados, filtros):
    termo = str(filtros.get('termo', '') or '').strip()
    setores = filtros.get('setores', []) or []
    status = filtros.get('status', []) or []
    fotos = filtros.get('foto', []) or []
    plaqueta = str(filtros.get('plaqueta', '') or '')
    if not isinstance(setores, list) or not isinstance(status, list) or not isinstance(fotos, list):
        raise ValueError('Os filtros de setor, status e foto devem ser listas.')

    data_inicial = str(filtros.get('data_inicial', '') or '')
    data_final = str(filtros.get('data_final', '') or '')
    try:
        if data_inicial:
            data_inicial = date.fromisoformat(data_inicial).strftime('%d/%m/%Y')
        if data_final:
            data_final = date.fromisoformat(data_final).strftime('%d/%m/%Y')
    except ValueError as error:
        raise ValueError('Informe um período de datas válido.') from error

    return aplicar_filtros_busca(
        dados,
        termo=termo,
        setores_selecionados=setores,
        status_selecionados=status,
        foto_selecionados=fotos,
        plaqueta=plaqueta,
        data_inicial=data_inicial,
        data_final=data_final,
    )


def _fontes_importadas():
    return {
        'csv': (DADOS_ENTRADA_DIR / 'Localizar_Bens.csv').is_file(),
        'alienados': (DADOS_ENTRADA_DIR / 'Bens_Alienados.xlsx').is_file(),
        'baixados': Path(ARQUIVO_BAIXADOS).is_file(),
    }


def _resumo_status():
    mysql = mysql_ativo()
    caminho = None if mysql else _arquivo_ativo()
    timestamp = ler_timestamp_preparacao()
    fontes = _fontes_importadas()
    dados = _carregar_base()
    if dados is None:
        return {
            'estado': 'sem_dados',
            'arquivo': 'MySQL' if mysql else None,
            'total': 0,
            'setores': 0,
            'lista_setores': [],
            'status': {},
            'ultima_preparacao': timestamp,
            'fontes': fontes,
            'fotos': {'referenciadas': 0, 'disponiveis': 0, 'pendentes': 0},
        }

    setores = _setores(dados)
    status = {}
    if 'STATUS' in dados.columns:
        status = {
            str(chave): int(valor)
            for chave, valor in dados['STATUS'].astype(str).str.upper().value_counts().items()
        }
    fotos_referenciadas = set()
    if 'Foto' in dados.columns:
        fotos_referenciadas = {
            Path(str(valor).replace('\\', '/')).name
            for valor in dados['Foto'].dropna()
            if str(valor).strip()
        }
    fotos_disponiveis = {
        nome for nome in fotos_referenciadas
        if (FOTOS_DIR / nome).is_file()
    }
    return {
        'estado': 'disponivel',
        'arquivo': 'MySQL' if mysql else caminho.name,
        'total': len(dados),
        'setores': len(setores),
        'lista_setores': setores,
        'status': status,
        'ultima_preparacao': timestamp,
        'fontes': fontes,
        'fotos': {
            'referenciadas': len(fotos_referenciadas),
            'disponiveis': len(fotos_disponiveis),
            'pendentes': len(fotos_referenciadas - fotos_disponiveis),
        },
    }


def _validar_referencias_foto(dados):
    if 'Foto' not in dados.columns:
        dados['Foto'] = ''
        return dados

    nomes = []
    for valor in dados['Foto'].fillna(''):
        referencia = str(valor).strip()
        if not referencia:
            nomes.append('')
            continue
        nome = Path(referencia.replace('\\', '/')).name
        nome_seguro = secure_filename(nome)
        if (
            not nome_seguro
            or nome_seguro != nome
            or Path(nome).suffix.lower() not in EXTENSOES_IMAGEM
        ):
            raise ValueError(
                'A coluna Foto contém uma referência de imagem inválida. '
                'Verifique os nomes dos arquivos na planilha.'
            )
        nomes.append(nome)
    dados['Foto'] = nomes
    return dados


def _csrf_required(funcao):
    @wraps(funcao)
    def validar_csrf(*args, **kwargs):
        token = session.get('csrf_token')
        enviado = request.headers.get('X-CSRF-Token')
        if not token or not enviado or token != enviado:
            abort(400, description='Sessão inválida. Atualize a página e tente novamente.')
        return funcao(*args, **kwargs)
    return validar_csrf


def _confirmar_substituicao(caminho):
    if caminho.exists() and request.form.get('confirmar_substituicao') != 'sim':
        abort(
            409,
            description='Este arquivo já existe. Confirme a substituição antes de importar.',
        )


def _temp_upload(upload, extensao_esperada):
    if upload is None or not upload.filename:
        abort(400, description='Selecione um arquivo para importar.')
    nome_seguro = secure_filename(upload.filename)
    if Path(nome_seguro).suffix.lower() != extensao_esperada:
        abort(400, description=f'O arquivo deve ter a extensão {extensao_esperada}.')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporario = tempfile.NamedTemporaryFile(
        prefix='importacao_', suffix=extensao_esperada, dir=DATA_DIR, delete=False
    )
    caminho = Path(temporario.name)
    temporario.close()
    try:
        upload.save(caminho)
    except OSError:
        caminho.unlink(missing_ok=True)
        raise
    return caminho


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


def _aplicar_alteracoes_item(dados, indice, alteracoes):
    for coluna, padrao in (
        ('STATUS', 'PEND'),
        ('ESTADO', ''),
        ('OBSERVACAO', ''),
        ('ALTERADO_EM', ''),
    ):
        if coluna not in dados.columns:
            dados[coluna] = padrao

    status = str(alteracoes.get('status', dados.at[indice, 'STATUS'])).strip().upper()
    estado = str(alteracoes.get('estado', dados.at[indice, 'ESTADO'])).strip().upper()
    observacao = str(
        alteracoes.get('observacao', dados.at[indice, 'OBSERVACAO'])
    ).strip()
    if status not in STATUS_OPCOES or estado not in ESTADO_OPCOES:
        abort(400, description='Status ou estado de conservação inválido.')
    if len(observacao) > 2000:
        abort(400, description='A observação excede 2.000 caracteres.')
    if status in ('N_LOC', 'BAIXA'):
        estado = 'NAO_APLICAVEL'
    elif estado == 'NAO_APLICAVEL':
        estado = 'EM USO'
    dados.at[indice, 'STATUS'] = status
    dados.at[indice, 'ESTADO'] = estado
    dados.at[indice, 'OBSERVACAO'] = observacao

    identificador = str(dados.at[indice, 'Identificador Provisório']) if (
        'Identificador Provisório' in dados.columns
    ) else ''
    if 'descricao' in alteracoes:
        if not (
            identificador.startswith('SEM-ID-')
            and not identificador.startswith('SEM-ID-ODS-')
        ):
            abort(400, description='A descrição deste bem patrimonial não pode ser alterada.')
        descricao = str(alteracoes['descricao']).strip()
        if not descricao or len(descricao) > 500:
            abort(400, description='Informe uma descrição de até 500 caracteres.')
        dados.at[indice, 'Descrição'] = descricao

    dados.at[indice, 'ALTERADO_EM'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    return dados


def _preparar_imagem(upload):
    if upload is None or not upload.filename:
        return None
    nome = secure_filename(upload.filename)
    extensao = Path(nome).suffix.lower()
    if extensao not in EXTENSOES_IMAGEM:
        abort(400, description='Formato de imagem não permitido.')
    FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    caminho = FOTOS_DIR / f'{token_hex(12)}{extensao}'
    try:
        upload.stream.seek(0)
        with Image.open(upload.stream) as imagem:
            imagem.verify()
        upload.stream.seek(0)
        upload.save(caminho)
    except (OSError, UnidentifiedImageError) as error:
        caminho.unlink(missing_ok=True)
        abort(400, description=f'Imagem inválida: {error}')
    return caminho.name


def create_app():
    garantir_pastas()
    aplicacao = Flask(__name__)
    aplicacao.secret_key = token_hex(32)
    aplicacao.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES

    @aplicacao.errorhandler(HTTPException)
    def erro_http(error):
        return jsonify(error=error.description), error.code

    @aplicacao.errorhandler(Exception)
    def erro_inesperado(error):
        aplicacao.logger.exception('Erro não tratado na aplicação local')
        return jsonify(error=f'Falha inesperada: {error}'), 500

    @aplicacao.get('/')
    def index():
        session.setdefault('csrf_token', token_urlsafe(32))
        return render_template('index.html', csrf_token=session['csrf_token'])

    @aplicacao.get('/api/status')
    def status():
        try:
            return jsonify(_resumo_status())
        except ValueError as error:
            return jsonify(
                estado='invalida',
                arquivo=Path(ARQUIVO_RESULTADO).name,
                total=0,
                setores=0,
                lista_setores=[],
                fontes=_fontes_importadas(),
                error=str(error),
            ), 409

    @aplicacao.post('/api/importar/csv')
    @_csrf_required
    def importar_csv():
        arquivo_destino = DADOS_ENTRADA_DIR / 'Localizar_Bens.csv'
        if arquivo_destino.exists() and request.form.get('confirmar_substituicao') != 'sim':
            abort(409, description='Já existe um CSV importado. Confirme a substituição.')
        temporario = _temp_upload(request.files.get('arquivo'), '.csv')
        try:
            validar_colunas(carregar_csv_entrada(str(temporario)))
            if arquivo_destino.exists():
                _criar_backup_web()
            destino, total = importar_csv_entrada(str(temporario))
        except (OSError, UnicodeDecodeError, pd.errors.ParserError, ValueError) as error:
            abort(400, description=f'Importação não realizada: {error}')
        finally:
            temporario.unlink(missing_ok=True)
        return jsonify(mensagem='CSV validado e importado.', registros=total, arquivo=Path(destino).name)

    @aplicacao.post('/api/importar/resultado')
    @_csrf_required
    def importar_resultado():
        temporario = _temp_upload(
            request.files.get('arquivo'), '.xlsx'
        )
        try:
            try:
                dados = pd.read_excel(temporario, dtype=object).fillna('')
            except (OSError, ValueError, ImportError, zipfile.BadZipFile) as error:
                abort(400, description=f'Planilha Excel inválida: {error}')

            obrigatorias = {'Plaqueta', 'Descrição', 'Setor Atual', 'STATUS'}
            faltantes = sorted(obrigatorias.difference(dados.columns))
            if faltantes:
                abort(
                    400,
                    description=(
                        'Planilha de resultado inválida. Colunas obrigatórias ausentes: '
                        + ', '.join(faltantes)
                    ),
                )
            if dados.empty:
                abort(400, description='A planilha de resultado não contém bens.')

            status = dados['STATUS'].fillna('').astype(str).str.strip().str.upper()
            status_invalidos = sorted(set(status) - set(STATUS_OPCOES))
            if status_invalidos:
                abort(
                    400,
                    description=(
                        'A planilha contém status não reconhecidos: '
                        + ', '.join(status_invalidos)
                    ),
                )
            dados['STATUS'] = status
            referencias_foto = 0
            try:
                dados = _validar_referencias_foto(dados)
            except ValueError as error:
                abort(400, description=str(error))
            if 'Foto' in dados.columns:
                referencias_foto = int(
                    dados['Foto'].fillna('').astype(str).str.strip().ne('').sum()
                )

            preview = {
                'bens': len(dados),
                'setores': dados['Setor Atual'].fillna('').astype(str).str.strip().replace('', pd.NA).nunique(),
                'status': {
                    str(chave): int(valor)
                    for chave, valor in dados['STATUS'].value_counts().items()
                },
                'referencias_foto_preservadas': referencias_foto,
            }
            if _arquivo_ativo() is not None and request.form.get('confirmado') != 'sim':
                return jsonify(
                    error=(
                        'A importação substituirá a base web atual. '
                        'Confirme após revisar o resumo.'
                    ),
                    preview=preview,
                    requires_confirmation=True,
                ), 409

            if _arquivo_ativo() is not None:
                _criar_backup_web()
            _salvar_planilha(dados)
            return jsonify(
                mensagem='Planilha de resultado migrada para a base web.',
                **preview,
            )
        finally:
            temporario.unlink(missing_ok=True)

    @aplicacao.post('/api/importar/auxiliar')
    @_csrf_required
    def importar_auxiliar():
        tipo = request.form.get('tipo', '')
        tipos = {
            'alienados': ('Bens_Alienados.xlsx', '.xlsx'),
            'baixados': ('Bens_Baixados.xlsx', '.xlsx'),
            'sem_plaqueta': (itens_sem_plaqueta.ARQUIVO_ORIGEM, '.ods'),
        }
        if tipo not in tipos:
            abort(400, description='Tipo de arquivo auxiliar desconhecido.')
        nome, extensao = tipos[tipo]
        destino = DADOS_ENTRADA_DIR / nome
        if destino.exists() and request.form.get('confirmar_substituicao') != 'sim':
            abort(409, description=f'Já existe {nome}. Confirme a substituição.')
        temporario = _temp_upload(request.files.get('arquivo'), extensao)
        try:
            if tipo == 'alienados':
                carregar_bens_alienados(str(temporario))
            elif tipo == 'baixados':
                carregar_bens_baixados(str(temporario))
            else:
                itens = carregar_itens_sem_plaqueta(str(temporario))
                if not {'descricao', 'setor atual'}.issubset(itens.columns):
                    raise ValueError(
                        'ODS inválido: são necessárias as colunas Descrição e Setor Atual.'
                    )
            if destino.exists():
                _criar_backup_web()
            os.replace(temporario, destino)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            abort(400, description=f'Importação não realizada: {error}')
        finally:
            temporario.unlink(missing_ok=True)
        return jsonify(mensagem=f'{nome} validado e importado.', arquivo=nome)

    @aplicacao.post('/api/preparar')
    @_csrf_required
    def preparar():
        csv = DADOS_ENTRADA_DIR / 'Localizar_Bens.csv'
        if not csv.is_file():
            abort(409, description='Importe um CSV patrimonial válido antes de preparar os dados.')
        if _arquivo_ativo() is not None and request.form.get('confirmado') != 'sim':
            abort(
                409,
                description='A preparação atualiza a base operacional. Confirme para continuar.',
            )
        _criar_backup_web()
        try:
            total = preparar_sistema_refinado()
            if mysql_ativo():
                dados_preparados = pd.read_excel(ARQUIVO_RESULTADO).fillna('')
                _salvar_planilha(dados_preparados)
            timestamp = salvar_timestamp_preparacao()
        except (OSError, ValueError, pd.errors.ParserError) as error:
            abort(400, description=f'Preparação interrompida: {error}')
        avisos = []
        if not (DADOS_ENTRADA_DIR / 'Bens_Alienados.xlsx').is_file():
            avisos.append('Nenhuma planilha de bens alienados importada; essa fonte foi ignorada.')
        baixados = carregar_bens_baixados()
        if baixados.empty:
            avisos.append('Nenhuma planilha de bens baixados importada; essa fonte foi ignorada.')
        if not baixados.empty:
            dados_preparados = _carregar_base()
            plaquetas_fonte = set(baixados['Plaqueta'])
            status_por_plaqueta = {}
            if dados_preparados is not None and 'STATUS' in dados_preparados.columns:
                for _, linha in dados_preparados.iterrows():
                    plaqueta = formatar_plaqueta_exibicao(linha.get('Plaqueta', ''))
                    status = str(linha.get('STATUS', '')).strip().upper()
                    if plaqueta and status:
                        status_por_plaqueta.setdefault(plaqueta, set()).add(status)
            plaquetas_base = set(status_por_plaqueta)
            encontradas = plaquetas_fonte & plaquetas_base
            baixas_aplicadas = {
                plaqueta for plaqueta in encontradas
                if 'BAIXA' in status_por_plaqueta.get(plaqueta, set())
            }
            alienadas = {
                plaqueta for plaqueta in encontradas
                if 'ALIEN' in status_por_plaqueta.get(plaqueta, set())
                and 'BAIXA' not in status_por_plaqueta.get(plaqueta, set())
            }
            if alienadas:
                avisos.append(
                    f'{len(alienadas)} plaqueta(s) da lista de baixados também '
                    'constam como alienadas; o status ALIEN foi mantido.'
                )
            if len(baixas_aplicadas) + len(alienadas) < len(encontradas):
                avisos.append(
                    f'{len(encontradas) - len(baixas_aplicadas) - len(alienadas)} '
                    'plaqueta(s) da planilha '
                    'de baixados não ficaram com status BAIXA. Verifique conflito '
                    'com bens alienados.'
                )
        return jsonify(
            mensagem='Dados SH3 preparados.',
            total=total,
            ultima_preparacao=timestamp,
            avisos=avisos,
            baixados={
                'na_planilha': len(baixados),
                'encontrados_no_cadastro': len(encontradas) if not baixados.empty else 0,
                'marcados_baixa': len(baixas_aplicadas) if not baixados.empty else 0,
                'prevaleceram_alienados': len(alienadas) if not baixados.empty else 0,
                'nao_encontrados': (
                    len(plaquetas_fonte - plaquetas_base) if not baixados.empty else 0
                ),
            },
        )

    @aplicacao.get('/api/setores')
    def setores():
        try:
            dados = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if dados is None:
            return jsonify(setores=[])
        return jsonify(setores=_setores(dados))

    @aplicacao.get('/api/conferencia')
    def conferencia():
        setor = request.args.get('setor', '').strip()
        if not setor:
            abort(400, description='Selecione um setor para iniciar a conferência.')
        try:
            dados = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if dados is None:
            abort(409, description='Ainda não há uma base preparada.')
        if setor not in _setores(dados):
            abort(404, description='O setor selecionado não foi encontrado na base.')
        dados = dados[dados['Setor Atual'].astype(str).str.strip() == setor].copy()
        total = len(dados)
        pagina, por_pagina, inicio, fim = _pagina_requisitada(total)
        return jsonify(
            setor=setor,
            total=total,
            pagina=pagina,
            por_pagina=por_pagina,
            itens=_registros_json(dados.iloc[inicio:fim]),
        )

    @aplicacao.get('/api/busca')
    def busca():
        try:
            dados = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if dados is None:
            return jsonify(total=0, itens=[])
        try:
            filtrados = _aplicar_filtros_busca(
                dados,
                {
                    'termo': request.args.get('termo', ''),
                    'setores': request.args.getlist('setor'),
                    'status': request.args.getlist('status'),
                    'foto': request.args.getlist('foto'),
                    'plaqueta': request.args.get('plaqueta', ''),
                    'data_inicial': request.args.get('data_inicial', ''),
                    'data_final': request.args.get('data_final', ''),
                },
            )
        except ValueError:
            abort(400, description='Informe as datas usando um período válido.')
        total = len(filtrados)
        pagina, por_pagina, inicio, fim = _pagina_requisitada(total)
        return jsonify(
            total=total,
            pagina=pagina,
            por_pagina=por_pagina,
            itens=_registros_json(filtrados.iloc[inicio:fim]),
        )

    @aplicacao.get('/api/leilao')
    def listar_itens_leilao():
        try:
            itens = _carregar_itens_leilao()
        except ValueError as error:
            abort(409, description=str(error))
        return jsonify(total=len(itens), itens=itens)

    @aplicacao.get('/api/leilao/<identificador>/sugestoes')
    def sugerir_vinculos_leilao(identificador):
        try:
            itens = _carregar_itens_leilao()
        except ValueError as error:
            abort(409, description=str(error))
        item = next((registro for registro in itens if registro.get('id') == identificador), None)
        if item is None:
            abort(404, description='Item da lista de leilão não encontrado.')
        try:
            base = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if base is None:
            abort(409, description='Prepare a base patrimonial antes de buscar correspondências.')
        return jsonify(sugestoes=_sugerir_correspondencias(item, base))

    @aplicacao.post('/api/leilao/importar')
    @_csrf_required
    def importar_lista_leilao():
        try:
            arquivo = request.files.get('arquivo')
            if arquivo is None or not arquivo.filename:
                abort(400, description='Selecione um arquivo Excel ou CSV.')
            novos = _ler_arquivo_leilao(arquivo)
            itens = _carregar_itens_leilao()
            itens.extend(novos)
            _salvar_itens_leilao(itens)
        except ValueError as error:
            abort(400, description=str(error))
        except OSError as error:
            abort(500, description=f'Não foi possível salvar a lista de leilão: {error}')
        return jsonify(
            mensagem=f'{len(novos)} item(ns) importado(s) como pendentes de identificação.',
            importados=len(novos),
            total=len(itens),
        )

    @aplicacao.post('/api/leilao/manual')
    @_csrf_required
    def incluir_item_leilao_manual():
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict):
            abort(400, description='Dados do item para leilão inválidos.')
        descricao = str(dados.get('descricao', '') or '').strip()
        if not descricao or len(descricao) > 500:
            abort(400, description='Informe uma descrição de até 500 caracteres.')
        item = {
            'id': token_hex(12),
            'plaqueta_informada': str(dados.get('plaqueta', '') or '').strip()[:100],
            'descricao': descricao,
            'setor': str(dados.get('setor', '') or '').strip()[:255],
            'data': str(dados.get('data', '') or '').strip()[:50],
            'situacao': 'PENDENTE_IDENTIFICACAO',
            'plaqueta_vinculada': '',
        }
        try:
            itens = _carregar_itens_leilao()
            itens.append(item)
            _salvar_itens_leilao(itens)
        except (OSError, ValueError) as error:
            abort(500, description=f'Não foi possível salvar o item: {error}')
        return jsonify(mensagem='Item adicionado como pendente de identificação.', item=item)

    @aplicacao.post('/api/leilao/<identificador>/editar')
    @_csrf_required
    def editar_item_leilao(identificador):
        alteracoes = request.get_json(silent=True)
        if not isinstance(alteracoes, dict):
            abort(400, description='Dados de edição do item para leilão inválidos.')
        descricao = str(alteracoes.get('descricao', '') or '').strip()
        if not descricao or len(descricao) > 500:
            abort(400, description='Informe uma descrição de até 500 caracteres.')
        plaqueta = str(alteracoes.get('plaqueta', '') or '').strip()
        setor = str(alteracoes.get('setor', '') or '').strip()
        data_item = str(alteracoes.get('data', '') or '').strip()
        if len(plaqueta) > 100 or len(setor) > 255 or len(data_item) > 50:
            abort(400, description='Plaqueta, setor ou data excede o limite permitido.')
        try:
            itens = _carregar_itens_leilao()
        except ValueError as error:
            abort(409, description=str(error))
        item = next((registro for registro in itens if registro.get('id') == identificador), None)
        if item is None:
            abort(404, description='Item da lista de leilão não encontrado.')
        if setor != str(item.get('setor', '') or '').strip():
            try:
                base = _carregar_base()
            except ValueError as error:
                abort(409, description=str(error))
            if setor and (base is None or setor not in _setores(base)):
                abort(400, description='Selecione um setor existente.')
        item.update({
            'descricao': descricao,
            'plaqueta_informada': plaqueta,
            'setor': setor,
            'data': data_item,
        })
        try:
            _salvar_itens_leilao(itens)
        except OSError as error:
            abort(500, description=f'Não foi possível salvar as alterações: {error}')
        return jsonify(mensagem='Item da lista de leilão atualizado.', item=item)

    @aplicacao.post('/api/leilao/<identificador>/excluir')
    @_csrf_required
    def excluir_item_leilao(identificador):
        try:
            itens = _carregar_itens_leilao()
        except ValueError as error:
            abort(409, description=str(error))
        item = next((registro for registro in itens if registro.get('id') == identificador), None)
        if item is None:
            abort(404, description='Item da lista de leilão não encontrado.')
        itens.remove(item)
        try:
            _salvar_itens_leilao(itens)
        except OSError as error:
            abort(500, description=f'Não foi possível excluir o item: {error}')
        return jsonify(mensagem='Item removido da lista de leilão.')

    @aplicacao.post('/api/leilao/<identificador>/vincular')
    @_csrf_required
    def vincular_item_leilao(identificador):
        dados_requisicao = request.get_json(silent=True)
        plaqueta = str(
            dados_requisicao.get('plaqueta', '') if isinstance(dados_requisicao, dict) else ''
        ).strip()
        if not plaqueta:
            abort(400, description='Informe a plaqueta confirmada para vincular.')
        try:
            base = _carregar_base()
            itens = _carregar_itens_leilao()
        except ValueError as error:
            abort(409, description=str(error))
        if base is None:
            abort(409, description='Prepare a base patrimonial antes de vincular itens.')
        plaquetas_base = {
            formatar_plaqueta_exibicao(valor)
            for valor in base.get('Plaqueta', pd.Series(dtype=object))
        }
        if plaqueta not in plaquetas_base:
            abort(404, description='A plaqueta informada não foi encontrada na base patrimonial.')
        item = next((registro for registro in itens if registro.get('id') == identificador), None)
        if item is None:
            abort(404, description='Item da lista de leilão não encontrado.')
        item['plaqueta_vinculada'] = plaqueta
        item['situacao'] = 'IDENTIFICADO'
        try:
            _salvar_itens_leilao(itens)
        except OSError as error:
            abort(500, description=f'Não foi possível salvar o vínculo: {error}')
        return jsonify(
            mensagem=f'Item vinculado à plaqueta {plaqueta}. Nenhuma baixa foi realizada.',
            item=item,
        )

    @aplicacao.post('/api/conferencia/incluir')
    @_csrf_required
    def incluir_item():
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict):
            abort(400, description='Dados do item inválidos.')
        try:
            base = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if base is None:
            abort(409, description='Prepare a base antes de incluir um bem.')
        setor = str(dados.get('setor', '')).strip()
        descricao = str(dados.get('descricao', '')).strip()
        if setor not in _setores(base):
            abort(400, description='Selecione um setor existente.')
        if not descricao or len(descricao) > 500:
            abort(400, description='Informe uma descrição de até 500 caracteres.')
        try:
            quantidade = int(dados.get('quantidade', 1))
        except (TypeError, ValueError):
            abort(400, description='Quantidade inválida.')
        if quantidade < 1 or quantidade > 999:
            abort(400, description='A quantidade deve estar entre 1 e 999.')
        status = str(dados.get('status', 'DIVERG')).strip().upper()
        estado = str(dados.get('estado', 'EM USO')).strip().upper()
        if status not in STATUS_OPCOES or estado not in ESTADO_OPCOES:
            abort(400, description='Status ou estado de conservação inválido.')
        if status in ('N_LOC', 'BAIXA'):
            estado = 'NAO_APLICAVEL'
        elif estado == 'NAO_APLICAVEL':
            estado = 'EM USO'
        observacao = str(dados.get('observacao', '')).strip()
        if len(observacao) > 2000:
            abort(400, description='A observação excede 2.000 caracteres.')
        novos = criar_registros_sem_plaqueta(
            quantidade,
            descricao,
            setor,
            status,
            estado,
            observacao or 'ITEM LOCALIZADO SEM PLAQUETA - PENDENTE DE CADASTRO',
        )
        for coluna in base.columns:
            if coluna not in novos.columns:
                novos[coluna] = ''
        for coluna in novos.columns:
            if coluna not in base.columns:
                base[coluna] = ''
        base = pd.concat([base, novos[base.columns]], ignore_index=True)
        _salvar_planilha(base)
        return jsonify(mensagem=f'{quantidade} bem(ns) incluído(s).', total=len(base))

    @aplicacao.post('/api/itens/<int:indice>/editar')
    @_csrf_required
    def editar_item(indice):
        alteracoes = request.get_json(silent=True)
        if not isinstance(alteracoes, dict):
            abort(400, description='Dados de edição inválidos.')
        try:
            dados = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if dados is None or indice not in dados.index:
            abort(404, description='Item não encontrado.')
        dados = _aplicar_alteracoes_item(dados, indice, alteracoes)
        _salvar_planilha(dados)
        return jsonify(mensagem='Conferência salva.')

    @aplicacao.post('/api/itens/<int:indice>/foto')
    @_csrf_required
    def atualizar_foto(indice):
        try:
            dados = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if dados is None or indice not in dados.index:
            abort(404, description='Item não encontrado.')
        if 'Foto' not in dados.columns:
            dados['Foto'] = ''
        foto_atual = str(dados.at[indice, 'Foto']).strip()
        foto_nova = ''
        caminho_novo = None
        alteracoes = {
            coluna: request.form[coluna]
            for coluna in ('status', 'estado', 'observacao', 'descricao')
            if coluna in request.form
        }
        if alteracoes:
            dados = _aplicar_alteracoes_item(dados, indice, alteracoes)

        if request.form.get('remover') == 'sim':
            dados.at[indice, 'Foto'] = ''
        else:
            upload = request.files.get('arquivo')
            if upload is None or not upload.filename:
                abort(400, description='Selecione uma imagem ou confirme a remoção.')
            foto_nova = _preparar_imagem(upload)
            caminho_novo = FOTOS_DIR / foto_nova
            dados.at[indice, 'Foto'] = foto_nova

        salvou = False
        try:
            _salvar_planilha(dados)
            salvou = True
        finally:
            if not salvou and caminho_novo is not None:
                caminho_novo.unlink(missing_ok=True)

        if foto_atual and foto_atual != foto_nova:
            antigo = FOTOS_DIR / Path(foto_atual).name
            if antigo.parent == FOTOS_DIR:
                antigo.unlink(missing_ok=True)
        return jsonify(
            mensagem='Foto e conferência atualizadas.',
            foto=foto_nova,
            pasta=str(FOTOS_DIR.relative_to(DATA_DIR)),
            planilhas=[
                Path(ARQUIVO_RESULTADO).name,
                Path(ARQUIVO_CAMPOS).name,
            ],
        )

    @aplicacao.post('/api/relatorios/pendencias')
    @_csrf_required
    def gerar_relatorio():
        try:
            dados = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if dados is None:
            abort(409, description='Não há base preparada para gerar o relatório.')
        referencia = DADOS_ENTRADA_DIR / 'Localizar_Bens.csv'
        if referencia.is_file():
            df_valores = carregar_csv_entrada(str(referencia))
            dados = enriquecer_com_dados_patrimoniais(dados, df_valores)
        resultado = gerar_relatorios.exportar_relatorio_gerencial(dados)
        return jsonify(
            mensagem='Relatório gerencial gerado.',
            arquivo=Path(resultado['arquivo']).name,
            total=resultado['total'],
            pendencias=resultado['pendencias'],
            abas=resultado['total_abas'],
        )

    @aplicacao.post('/api/relatorios/busca/<formato>')
    @_csrf_required
    def gerar_relatorio_busca(formato):
        if formato not in ('excel', 'pdf'):
            abort(404, description='Formato de relatório não suportado.')
        filtros = request.get_json(silent=True)
        if not isinstance(filtros, dict):
            abort(400, description='Filtros da pesquisa inválidos.')
        try:
            dados = _carregar_base()
            if dados is None:
                abort(409, description='Ainda não há uma base para pesquisar.')
            filtrados = _aplicar_filtros_busca(dados, filtros)
        except ValueError as error:
            abort(400, description=str(error))
        if filtrados.empty:
            abort(400, description='A pesquisa não retornou itens para exportar.')

        RELATORIOS_DIR.mkdir(parents=True, exist_ok=True)
        extensao = 'xlsx' if formato == 'excel' else 'pdf'
        caminho = RELATORIOS_DIR / (
            f'relatorio_busca_{datetime.now():%Y%m%d_%H%M%S_%f}.{extensao}'
        )
        if formato == 'excel':
            utils.exportar_relatorio_busca(filtrados, caminho_saida=str(caminho))
        else:
            filtros_pdf = {
                'termo': str(filtros.get('termo', '') or ''),
                'setores': filtros.get('setores', []),
                'status': filtros.get('status', []),
                'plaqueta': filtros.get('plaqueta', ''),
                'data_inicial': filtros.get('data_inicial', ''),
                'data_final': filtros.get('data_final', ''),
            }
            utils.exportar_relatorio_busca_pdf(
                filtrados, caminho_saida=str(caminho), filtros=filtros_pdf
            )
        return jsonify(
            mensagem='Relatório da pesquisa gerado.',
            arquivo=caminho.name,
            total=len(filtrados),
            formato=formato,
        )

    @aplicacao.get('/api/relatorios/<path:nome>')
    def baixar_relatorio(nome):
        nome_seguro = secure_filename(nome)
        if nome_seguro != nome:
            abort(404, description='Relatório não encontrado.')
        return send_from_directory(RELATORIOS_DIR, nome_seguro, as_attachment=True)

    @aplicacao.post('/api/backups')
    @_csrf_required
    def criar_backup():
        caminho = _criar_backup_web()
        return jsonify(mensagem='Backup criado.', arquivo=caminho.name)

    @aplicacao.get('/api/backups')
    def listar_backups():
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        backups = []
        for caminho in sorted(BACKUPS_DIR.glob('backup_web_*.zip'), reverse=True):
            stat = caminho.stat()
            backups.append({
                'arquivo': caminho.name,
                'data': datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(
                    timespec='minutes'
                ),
                'tamanho': stat.st_size,
            })
        return jsonify(backups=backups)

    @aplicacao.get('/api/backups/<path:nome>')
    def baixar_backup(nome):
        if secure_filename(nome) != nome:
            abort(404, description='Backup não encontrado.')
        return send_from_directory(BACKUPS_DIR, nome, as_attachment=True)

    @aplicacao.post('/api/backups/<path:nome>/restaurar')
    @_csrf_required
    def restaurar_backup_web(nome):
        if secure_filename(nome) != nome:
            abort(404, description='Backup não encontrado.')
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict) or dados.get('confirmado') is not True:
            abort(
                400,
                description='Confirme que os dados web atuais serão substituídos.',
            )
        try:
            _restaurar_backup(nome)
            _sincronizar_mysql_com_planilha()
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            abort(400, description=f'Restauração não realizada: {error}')
        return jsonify(mensagem='Backup restaurado. Os dados web anteriores foram salvos em um novo backup.')

    @aplicacao.get('/dados-web/fotos/<path:nome>')
    def obter_foto(nome):
        nome_seguro = secure_filename(nome)
        if nome_seguro != nome or Path(nome_seguro).suffix.lower() not in EXTENSOES_IMAGEM:
            abort(404, description='Imagem não encontrada.')
        caminho = FOTOS_DIR / nome_seguro
        if request.args.get('miniatura') == '1':
            try:
                stat = caminho.stat()
                conteudo, mime_type = _miniatura_foto(
                    nome_seguro, stat.st_mtime_ns, stat.st_size
                )
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                abort(404, description='Imagem não encontrada ou inválida.')
            resposta = send_file(BytesIO(conteudo), mimetype=mime_type)
            resposta.cache_control.public = True
            resposta.cache_control.max_age = 86400
            return resposta
        try:
            with Image.open(caminho) as imagem:
                formato = imagem.format
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            abort(404, description='Imagem não encontrada ou inválida.')
        mime_type = PHOTO_MIME_TYPES.get(formato)
        if mime_type is None:
            abort(404, description='Formato de imagem não suportado.')
        return send_from_directory(FOTOS_DIR, nome_seguro, mimetype=mime_type)

    return aplicacao


app = create_app()


if __name__ == '__main__':
    host = os.environ.get('PATRIMONIO_WEB_HOST', '0.0.0.0')
    port = 8765
    if mysql_ativo() and not os.environ.get('PATRIMONIO_MYSQL_PASSWORD'):
        os.environ['PATRIMONIO_MYSQL_PASSWORD'] = getpass.getpass(
            'Senha MySQL do patrimonio_app (nao sera exibida): '
        )
    print('Acesso neste computador: http://127.0.0.1:8765')
    if host == '0.0.0.0':
        print('Acesso pela rede local (sem autenticação; use somente em rede privada):')
        try:
            enderecos = sorted({
                info[4][0]
                for info in socket.getaddrinfo(
                    socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
                )
                if not info[4][0].startswith('127.')
            })
        except OSError:
            enderecos = []
        if enderecos:
            for endereco in enderecos:
                print(f'  http://{endereco}:{port}')
        else:
            print('  Não foi possível detectar o IPv4; consulte "ipconfig".')
    app.run(host=host, port=port, debug=False, threaded=False)
