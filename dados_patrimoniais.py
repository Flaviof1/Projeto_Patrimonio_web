"""Funções compartilhadas para os dados financeiros do patrimônio."""

import re

import pandas as pd

from paths import DADOS_ENTRADA_DIR, localizar_arquivo


ARQUIVO_LOCALIZAR = localizar_arquivo('Localizar_Bens.csv', DADOS_ENTRADA_DIR)

# Estes campos acompanham a conferência, mas não são alterados pelas telas de campo.
COLUNAS_PATRIMONIAIS = [
    'Classificação',
    'Valor Inicial',
    'Vida Útil Inicial',
    'Vida Útil Atual',
    'Valor Residual',
    'Valor Atual',
]


def carregar_dados_patrimoniais(path=None):
    """Carrega a base oficial preservando zeros à esquerda dos identificadores."""
    caminho = path or ARQUIVO_LOCALIZAR
    if not caminho:
        return None
    try:
        return pd.read_csv(
            caminho,
            sep=';',
            encoding='latin1',
            dtype={'Código': str, 'Plaqueta': str},
            on_bad_lines='error',
        ).fillna('')
    except (OSError, UnicodeDecodeError, pd.errors.ParserError):
        return None


def _normalizar_codigo(valor):
    texto = str(valor).strip().upper()
    if texto.endswith('.0') and texto[:-2].isdigit():
        texto = texto[:-2]
    if texto.isdigit():
        return texto.lstrip('0') or '0'
    return texto


def _normalizar_plaqueta(valor):
    texto = str(valor).strip().upper()
    if texto.endswith('.0') and texto[:-2].isdigit():
        texto = texto[:-2]
    letras = ''.join(re.findall(r'[A-Z]+', texto))
    numeros = ''.join(re.findall(r'\d+', texto))
    if not numeros:
        return letras
    return f'{letras}{numeros[-6:].zfill(6)}'


def enriquecer_com_dados_patrimoniais(df_conferencia, df_patrimonial=None):
    """Preenche somente valores ausentes, por código e, como alternativa, plaqueta.

    Dados já registrados na conferência têm prioridade; por isso esta função não
    sobrescreve nenhuma informação operacional nem valores que já existam.
    """
    dados = df_conferencia.copy()
    referencia = df_patrimonial if df_patrimonial is not None else carregar_dados_patrimoniais()
    if referencia is None or referencia.empty:
        return dados

    campos = [campo for campo in COLUNAS_PATRIMONIAIS if campo in referencia.columns]
    if not campos:
        return dados

    for campo in campos:
        if campo not in dados.columns:
            dados[campo] = ''
        # A planilha de conferência pode trazer campos como Classificação
        # tipados como texto, enquanto a base oficial os lê como número.
        # object aceita ambos sem alterar os valores já existentes.
        dados[campo] = dados[campo].astype(object)

    por_codigo = {}
    por_plaqueta = {}
    for _, linha in referencia.iterrows():
        codigo = _normalizar_codigo(linha.get('Código', ''))
        plaqueta = _normalizar_plaqueta(linha.get('Plaqueta', ''))
        valores = {campo: linha.get(campo, '') for campo in campos}
        if codigo and codigo not in por_codigo:
            por_codigo[codigo] = valores
        if plaqueta and plaqueta not in por_plaqueta:
            por_plaqueta[plaqueta] = valores

    for indice, linha in dados.iterrows():
        valores = por_codigo.get(_normalizar_codigo(linha.get('Código', '')))
        if valores is None:
            valores = por_plaqueta.get(
                _normalizar_plaqueta(linha.get('Plaqueta', '')))
        if valores is None:
            continue
        for campo, valor in valores.items():
            atual = linha.get(campo, '')
            if pd.isna(atual) or str(atual).strip() == '':
                dados.at[indice, campo] = valor

    return dados


def anexar_colunas_csv_sh3(df_conferencia, df_sh3):
    """Acrescenta ao relatório as colunas originais do SH3 sem sobrescrever a conferência."""
    dados = df_conferencia.copy()
    if df_sh3 is None or df_sh3.empty:
        return dados

    colunas_saida = {}
    for coluna in df_sh3.columns:
        nome_saida = str(coluna)
        if nome_saida in dados.columns:
            nome_saida = f'SH3 - {nome_saida}'
        while nome_saida in dados.columns or nome_saida in colunas_saida.values():
            nome_saida = f'SH3 - {nome_saida}'
        colunas_saida[coluna] = nome_saida
        dados[nome_saida] = pd.Series('', index=dados.index, dtype=object)

    por_codigo = {}
    por_plaqueta = {}
    for _, linha in df_sh3.iterrows():
        valores = {
            colunas_saida[coluna]: linha[coluna]
            for coluna in df_sh3.columns
        }
        codigo = _normalizar_codigo(linha.get('Código', ''))
        plaqueta = _normalizar_plaqueta(linha.get('Plaqueta', ''))
        if codigo and codigo not in por_codigo:
            por_codigo[codigo] = valores
        if plaqueta and plaqueta not in por_plaqueta:
            por_plaqueta[plaqueta] = valores

    for indice, linha in dados.iterrows():
        valores = por_codigo.get(_normalizar_codigo(linha.get('Código', '')))
        if valores is None:
            valores = por_plaqueta.get(
                _normalizar_plaqueta(linha.get('Plaqueta', '')))
        if valores is None:
            continue
        for coluna, valor in valores.items():
            dados.at[indice, coluna] = valor

    return dados
