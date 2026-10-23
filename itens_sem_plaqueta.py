"""Importação segura dos bens físicos registrados sem plaqueta oficial."""

import hashlib
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd


NAMESPACES = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
}
ARQUIVO_ORIGEM = 'itens_inclusos_sem_plaquetas.ods'
COLUNA_IDENTIFICADOR = 'Identificador Provisório'
COLUNA_ORIGEM = 'Origem do Registro'


def _normalizar_texto(valor):
    texto = str(valor or '').strip().lower()
    texto = unicodedata.normalize('NFKD', texto)
    return ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))


def _texto_celula(celula):
    paragrafos = celula.findall('.//text:p', NAMESPACES)
    return '\n'.join(''.join(paragrafo.itertext()).strip() for paragrafo in paragrafos).strip()


def _linhas_ods(caminho):
    with zipfile.ZipFile(caminho) as arquivo:
        with arquivo.open('content.xml') as conteudo:
            raiz = ET.parse(conteudo).getroot()

    tabela = raiz.find('.//table:table', NAMESPACES)
    if tabela is None:
        return []

    linhas = []
    for linha in tabela.findall('table:table-row', NAMESPACES):
        valores = []
        for celula in linha:
            if not celula.tag.endswith('table-cell') and not celula.tag.endswith('covered-table-cell'):
                continue
            repeticoes = int(celula.get(f'{{{NAMESPACES["table"]}}}number-columns-repeated', '1'))
            valores.extend([_texto_celula(celula)] * repeticoes)
        repeticoes_linha = int(linha.get(f'{{{NAMESPACES["table"]}}}number-rows-repeated', '1'))
        if any(valor.strip() for valor in valores):
            linhas.extend([valores] * repeticoes_linha)
    return linhas


def carregar_itens_sem_plaqueta(caminho):
    """Lê a planilha ODS sem exigir dependências adicionais no computador."""
    try:
        linhas = _linhas_ods(caminho)
    except (OSError, ET.ParseError, zipfile.BadZipFile):
        return pd.DataFrame()

    if len(linhas) < 2:
        return pd.DataFrame()

    cabecalhos = [_normalizar_texto(valor) for valor in linhas[0]]
    registros = []
    for valores in linhas[1:]:
        registro = dict(zip(cabecalhos, valores))
        if any(str(valor).strip() for valor in registro.values()):
            registros.append(registro)
    return pd.DataFrame(registros)


def _normalizar_estado(valor):
    estado = _normalizar_texto(valor)
    if 'inserv' in estado:
        return 'INSERVIVEL'
    if 'desuso' in estado:
        return 'DESUSO'
    if 'nao aplic' in estado:
        return 'NAO_APLICAVEL'
    return 'EM USO'


def _identificador_estavel(descricao, setor, estado, observacao, ocorrencia):
    texto = '|'.join([descricao, setor, estado, observacao, str(ocorrencia)])
    codigo = hashlib.sha1(texto.encode('utf-8')).hexdigest()[:10].upper()
    return f'SEM-ID-ODS-{codigo}'


def converter_para_conferencia(itens):
    """Converte os achados em linhas operacionais, sem código patrimonial inventado."""
    if itens.empty:
        return pd.DataFrame()

    ocorrencias = {}
    registros = []
    for _, item in itens.iterrows():
        plaqueta = str(item.get('plaqueta', '')).strip()
        descricao = str(item.get('descricao', '')).strip()
        setor = str(item.get('setor atual', '')).strip()
        estado = _normalizar_estado(item.get('estado', ''))
        observacao_origem = str(item.get('observacao', '')).strip()
        assinatura = '|'.join([plaqueta, descricao, setor, estado, observacao_origem])
        ocorrencias[assinatura] = ocorrencias.get(assinatura, 0) + 1

        observacao = 'ITEM LOCALIZADO SEM PLAQUETA - PENDENTE DE CADASTRO'
        if observacao_origem:
            observacao = f'{observacao} | {observacao_origem}'

        registros.append({
            'Código': '',
            'Plaqueta': plaqueta,
            'Descrição': descricao,
            'Modelo': '',
            'Setor Atual': setor,
            'Data': '',
            COLUNA_IDENTIFICADOR: _identificador_estavel(
                descricao, setor, estado, observacao_origem, ocorrencias[assinatura]
            ),
            COLUNA_ORIGEM: ARQUIVO_ORIGEM,
            'STATUS': 'DIVERG',
            'ESTADO': estado,
            'OBSERVACAO': observacao,
            'ALTERADO_EM': '',
        })
    return pd.DataFrame(registros)
