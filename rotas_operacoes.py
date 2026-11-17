from flask import Blueprint

import gerar_relatorios
import itens_sem_plaqueta
import os
import pandas as pd
import utils
import zipfile
from PIL import (
    Image,
    UnidentifiedImageError,
)
from constantes import (
    STATUS_OPCOES,
)
from dados_patrimoniais import (
    anexar_colunas_csv_sh3,
    enriquecer_com_dados_patrimoniais,
)
from datetime import (
    datetime,
)
from flask import (
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
)
from io import (
    BytesIO,
)
from itens_sem_plaqueta import (
    carregar_itens_sem_plaqueta,
)
from mysql_store import (
    mysql_ativo,
)
from pathlib import (
    Path,
)
from paths import (
    ARQUIVO_RESULTADO,
    BACKUPS_DIR,
    DADOS_ENTRADA_DIR,
    FOTOS_DIR,
    RELATORIOS_DIR,
    salvar_timestamp_preparacao,
)
from preparar_dados import (
    carregar_bens_alienados,
    carregar_bens_baixados,
    carregar_csv_entrada,
    formatar_plaqueta_exibicao,
    importar_csv_entrada,
    preparar_sistema_refinado,
    validar_colunas,
)
from secrets import (
    token_urlsafe,
)
from werkzeug.utils import (
    secure_filename,
)
from servicos_comuns import (
    _csrf_required,
    _temp_upload,
)
from servicos_operacoes import (
    _criar_backup_web,
    _restaurar_backup,
    _salvar_planilha,
    _sincronizar_mysql_com_planilha,
)
from servicos_patrimonio import (
    EXTENSOES_IMAGEM,
    PHOTO_MIME_TYPES,
    _aplicar_filtros_busca,
    _arquivo_ativo,
    _carregar_base,
    _fontes_importadas,
    _miniatura_foto,
    _resumo_status,
    _validar_referencias_foto,
)

blueprint = Blueprint('operacoes', __name__)

@blueprint.get('/')
def index():
    session.setdefault('csrf_token', token_urlsafe(32))
    return render_template('index.html', csrf_token=session['csrf_token'])

@blueprint.get('/api/status')
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

@blueprint.post('/api/importar/csv')
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

@blueprint.post('/api/importar/resultado')
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

@blueprint.post('/api/importar/auxiliar')
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

@blueprint.post('/api/preparar')
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


@blueprint.post('/api/relatorios/pendencias')
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

@blueprint.post('/api/relatorios/busca/<formato>')
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
        if formato == 'excel':
            referencia = DADOS_ENTRADA_DIR / 'Localizar_Bens.csv'
            if referencia.is_file():
                filtrados = anexar_colunas_csv_sh3(
                    filtrados,
                    carregar_csv_entrada(str(referencia)),
                )
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

@blueprint.get('/api/relatorios/<path:nome>')
def baixar_relatorio(nome):
    nome_seguro = secure_filename(nome)
    if nome_seguro != nome:
        abort(404, description='Relatório não encontrado.')
    return send_from_directory(RELATORIOS_DIR, nome_seguro, as_attachment=True)

@blueprint.post('/api/backups')
@_csrf_required
def criar_backup():
    caminho = _criar_backup_web()
    return jsonify(mensagem='Backup criado.', arquivo=caminho.name)

@blueprint.get('/api/backups')
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

@blueprint.get('/api/backups/<path:nome>')
def baixar_backup(nome):
    if secure_filename(nome) != nome:
        abort(404, description='Backup não encontrado.')
    return send_from_directory(BACKUPS_DIR, nome, as_attachment=True)

@blueprint.post('/api/backups/<path:nome>/restaurar')
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

@blueprint.get('/dados-web/fotos/<path:nome>')
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
