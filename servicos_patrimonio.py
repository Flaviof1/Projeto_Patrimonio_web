import pandas as pd
from PIL import (
    Image,
    ImageOps,
    UnidentifiedImageError,
)
from busca_web import (
    aplicar_filtros_busca,
)
from constantes import (
    ESTADO_OPCOES,
    STATUS_OPCOES,
)
from datetime import (
    date,
    datetime,
)
from flask import (
    abort,
)
from functools import (
    lru_cache,
)
from io import (
    BytesIO,
)
from mysql_store import (
    MySQLStore,
    mysql_ativo,
)
from pathlib import (
    Path,
)
from paths import (
    ARQUIVO_CAMPOS,
    ARQUIVO_RESULTADO,
    DADOS_ENTRADA_DIR,
    FOTOS_DIR,
    ler_timestamp_preparacao,
)
from preparar_dados import (
    ARQUIVO_BAIXADOS,
    formatar_plaqueta,
    marcar_duplicidades,
)
from secrets import (
    token_hex,
)
from werkzeug.utils import (
    secure_filename,
)
from servicos_comuns import (
    _normalizar_valor_leilao,
)


EXTENSOES_IMAGEM = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
PHOTO_MIME_TYPES = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'BMP': 'image/bmp',
    'GIF': 'image/gif',
    'WEBP': 'image/webp',
}

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

def _aplicar_alteracoes_item(dados, indice, alteracoes):
    for coluna, padrao in (
        ('STATUS', 'PEND'),
        ('ESTADO', ''),
        ('OBSERVACAO', ''),
        ('Valor', ''),
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
    valor = dados.at[indice, 'Valor']
    if 'valor' in alteracoes:
        try:
            valor = _normalizar_valor_leilao(alteracoes['valor'])
        except ValueError as error:
            abort(400, description=str(error))
    if status in ('N_LOC', 'BAIXA'):
        estado = 'NAO_APLICAVEL'
    elif estado == 'NAO_APLICAVEL':
        estado = 'EM USO'
    dados.at[indice, 'STATUS'] = status
    dados.at[indice, 'ESTADO'] = estado
    dados.at[indice, 'OBSERVACAO'] = observacao
    dados.at[indice, 'Valor'] = valor

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

def _fontes_importadas():
    return {
        'csv': (DADOS_ENTRADA_DIR / 'Localizar_Bens.csv').is_file(),
        'alienados': (DADOS_ENTRADA_DIR / 'Bens_Alienados.xlsx').is_file(),
        'baixados': Path(ARQUIVO_BAIXADOS).is_file(),
    }
