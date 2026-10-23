import pandas as pd


def filtrar_por_periodo(df, data_inicial='', data_final='', coluna='Data'):
    if coluna not in df.columns:
        return df

    inicio = pd.to_datetime(
        data_inicial.strip(), dayfirst=True, errors='coerce'
    ) if data_inicial.strip() else None
    fim = pd.to_datetime(
        data_final.strip(), dayfirst=True, errors='coerce'
    ) if data_final.strip() else None

    if (inicio is None and fim is None) or (
        inicio is not None and pd.isna(inicio)
    ) or (fim is not None and pd.isna(fim)):
        return df

    datas = pd.to_datetime(df[coluna], dayfirst=True, errors='coerce')
    filtro = pd.Series(True, index=df.index)
    if inicio is not None:
        filtro &= datas >= inicio.normalize()
    if fim is not None:
        filtro &= datas <= fim.normalize()
    return df[filtro]


def aplicar_filtros_busca(
    df,
    termo='',
    setores_selecionados=None,
    status_selecionados=None,
    foto_selecionados=None,
    plaqueta='',
    data_inicial='',
    data_final='',
):
    if df is None or df.empty:
        return df

    dados = df.copy()
    setores_selecionados = setores_selecionados or []
    status_selecionados = status_selecionados or []
    foto_selecionados = foto_selecionados or []

    if termo:
        termo = termo.strip().lower()
        filtro_texto = pd.Series(False, index=dados.index)
        for coluna in dados.columns:
            filtro_texto |= dados[coluna].astype(str).str.lower().str.contains(
                termo, regex=False
            )
        dados = dados[filtro_texto].copy()

    if setores_selecionados and 'Setor Atual' in dados.columns:
        setores = [str(item).strip() for item in setores_selecionados]
        dados = dados[dados['Setor Atual'].astype(str).isin(setores)].copy()

    if status_selecionados and 'STATUS' in dados.columns:
        status = {str(item).upper() for item in status_selecionados}
        dados = dados[dados['STATUS'].astype(str).str.upper().isin(status)].copy()

    if foto_selecionados and 'Foto' in dados.columns:
        tem_foto = dados['Foto'].fillna('').astype(str).str.strip().ne('')
        incluir = pd.Series(False, index=dados.index)
        if 'Com foto' in foto_selecionados:
            incluir |= tem_foto
        if 'Sem foto' in foto_selecionados:
            incluir |= ~tem_foto
        dados = dados[incluir].copy()

    if plaqueta and 'Plaqueta' in dados.columns:
        tem_plaqueta = dados['Plaqueta'].fillna('').astype(str).str.strip().ne('')
        if plaqueta == 'Sem plaqueta':
            dados = dados[~tem_plaqueta].copy()
        elif plaqueta == 'Com plaqueta':
            dados = dados[tem_plaqueta].copy()
        else:
            raise ValueError('O filtro de plaqueta é inválido.')

    return filtrar_por_periodo(dados, data_inicial, data_final)
