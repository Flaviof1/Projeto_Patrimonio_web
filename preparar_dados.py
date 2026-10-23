import os
import json
import re
import shutil
import uuid
import csv
import io
import zipfile
from datetime import datetime
import pandas as pd

from paths import DADOS_ENTRADA_DIR, DADOS_TRABALHO_DIR, caminho_trabalho, criar_versao_resultado, garantir_pastas, localizar_arquivo
from dados_patrimoniais import COLUNAS_PATRIMONIAIS
from itens_sem_plaqueta import (
    ARQUIVO_ORIGEM as NOME_ARQUIVO_ITENS_SEM_PLAQUETA,
    COLUNA_ORIGEM as COLUNA_ORIGEM_REGISTRO,
    carregar_itens_sem_plaqueta,
    converter_para_conferencia,
)

ARQUIVO_ENTRADA = localizar_arquivo('Localizar_Bens.csv', DADOS_ENTRADA_DIR)
ARQUIVO_ALIENADOS = localizar_arquivo('Bens_Alienados.xlsx', DADOS_ENTRADA_DIR)
ARQUIVO_BAIXADOS = localizar_arquivo('Bens_Baixados.xlsx', DADOS_ENTRADA_DIR)
ARQUIVO_SAIDA = caminho_trabalho('conferencia_campo.xlsx')
ARQUIVO_RESULTADO = caminho_trabalho('conferencia_resultado.xlsx')
ARQUIVO_BACKUP_HISTORICO = caminho_trabalho(
    'conferencia_resultado_backup_atual.xlsx')
ARQUIVO_ITENS_SEM_PLAQUETAS = str(
    DADOS_ENTRADA_DIR / NOME_ARQUIVO_ITENS_SEM_PLAQUETA
)
ARQUIVO_SETORES_EXCLUIDOS = caminho_trabalho('setores_excluidos.json')
COLUNAS_OBRIGATORIAS = ['Código', 'Plaqueta',
                        'Descrição', 'Modelo', 'Setor Atual', 'Data']
NOME_ARQUIVO_ENTRADA = 'Localizar_Bens.csv'
COLUNA_PLAQUETA_ORIGINAL = 'Plaqueta Original'
COLUNA_PLAQUETA_NORMALIZADA = 'Plaqueta Normalizada'
COLUNA_CHAVE_CONFERENCIA = 'Chave Conferencia'
COLUNA_IDENTIFICADOR_PROVISORIO = 'Identificador Provisório'
COLUNA_DUPLICIDADE_EXATA = 'Duplicidade Plaqueta'
COLUNA_DUPLICIDADE_NUMERICA = 'Possivel Duplicidade Plaqueta'
COLUNAS_HISTORICO_ALIENACAO = [
    'STATUS_ANTERIOR',
    'SETOR_ANTERIOR',
    'OBSERVACAO_ANTERIOR',
    'DATA_SINCRONIZACAO',
]


def gerar_identificador_provisorio():
    return f'SEM-ID-{uuid.uuid4().hex[:8].upper()}'


def criar_registros_sem_plaqueta(
        quantidade, descricao, setor, status, estado, observacao):
    quantidade = int(quantidade)
    if quantidade < 1:
        raise ValueError('A quantidade deve ser maior que zero.')

    agora = datetime.now()
    return pd.DataFrame([
        {
            'Código': '',
            'Plaqueta': '',
            'Plaqueta Original': '',
            'Plaqueta Normalizada': '',
            'Descrição': descricao,
            'Modelo': '',
            'Setor Atual': setor,
            'Data': agora.strftime('%d/%m/%Y'),
            'Chave Conferencia': '|',
            'Duplicidade Plaqueta': '',
            'Possivel Duplicidade Plaqueta': '',
            'Identificador Provisório': gerar_identificador_provisorio(),
            'STATUS': status,
            'ESTADO': estado,
            'OBSERVACAO': observacao,
            'ALTERADO_EM': agora.strftime('%d/%m/%Y %H:%M:%S'),
        }
        for _ in range(quantidade)
    ])


def normalizar_codigo(valor):
    return str(valor).strip()


def normalizar_plaqueta_numerica(valor):
    texto_original = str(valor).strip()
    if texto_original.endswith('.0') and texto_original[:-2].isdigit():
        texto_original = texto_original[:-2]
    texto = ''.join(ch for ch in texto_original if ch.isdigit())
    if not texto:
        return ''
    return texto[-6:].zfill(6)


def formatar_plaqueta_exibicao(valor):
    texto = str(valor).strip().upper()
    if not texto or texto.lower() == 'nan':
        return ''

    letras = ''.join(re.findall(r'[A-Z]+', texto))
    numero = normalizar_plaqueta_numerica(texto)
    if not numero:
        return letras
    return f'{letras}{numero}' if letras else numero


def criar_chave_conferencia(codigo, plaqueta, identificador_provisorio=''):
    if not normalizar_codigo(codigo) and not formatar_plaqueta_exibicao(plaqueta):
        identificador = str(identificador_provisorio).strip().upper()
        if identificador:
            return f'PROV|{identificador}'
    return f'{normalizar_codigo(codigo)}|{formatar_plaqueta_exibicao(plaqueta)}'


def formatar_plaqueta(df, coluna='Plaqueta'):
    if coluna not in df.columns:
        return df

    if COLUNA_PLAQUETA_ORIGINAL not in df.columns:
        posicao = df.columns.get_loc(coluna) + 1
        df.insert(posicao, COLUNA_PLAQUETA_ORIGINAL,
                  df[coluna].fillna('').astype(str).str.strip())
    else:
        df[COLUNA_PLAQUETA_ORIGINAL] = df[COLUNA_PLAQUETA_ORIGINAL].fillna(
            '').astype(str).str.strip()

    df[coluna] = df[coluna].fillna('').apply(formatar_plaqueta_exibicao)
    df[COLUNA_PLAQUETA_NORMALIZADA] = df[coluna].apply(
        normalizar_plaqueta_numerica)

    if COLUNA_IDENTIFICADOR_PROVISORIO not in df.columns:
        df[COLUNA_IDENTIFICADOR_PROVISORIO] = ''

    sem_identificacao = (
        df.get('Código', pd.Series('', index=df.index)).fillna('').astype(str).str.strip().eq('') &
        df[coluna].fillna('').astype(str).str.strip().eq('')
    )
    identificadores = df[COLUNA_IDENTIFICADOR_PROVISORIO].fillna(
        '').astype(str).str.strip()
    for indice in df.index[sem_identificacao & identificadores.eq('')]:
        df.at[indice, COLUNA_IDENTIFICADOR_PROVISORIO] = gerar_identificador_provisorio()

    if 'Código' in df.columns:
        df[COLUNA_CHAVE_CONFERENCIA] = [
            criar_chave_conferencia(codigo, plaqueta, identificador)
            for codigo, plaqueta, identificador in zip(
                df['Código'], df[coluna], df[COLUNA_IDENTIFICADOR_PROVISORIO]
            )
        ]

    return df


def marcar_duplicidades(df):
    if 'Plaqueta' not in df.columns:
        return df

    plaquetas = df['Plaqueta'].fillna('').astype(str).str.strip()
    normalizadas = df.get(COLUNA_PLAQUETA_NORMALIZADA,
                          plaquetas.apply(normalizar_plaqueta_numerica))

    duplicidade_exata = plaquetas.ne('') & plaquetas.duplicated(keep=False)
    duplicidade_numerica = normalizadas.ne(
        '') & normalizadas.duplicated(keep=False)

    df[COLUNA_DUPLICIDADE_EXATA] = duplicidade_exata.map(
        {True: 'SIM', False: ''})
    df[COLUNA_DUPLICIDADE_NUMERICA] = duplicidade_numerica.map(
        {True: 'SIM', False: ''})
    return df


def carregar_csv_entrada(path=ARQUIVO_ENTRADA):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Arquivo de entrada não encontrado: {path}")

    with open(path, 'r', encoding='latin1', newline='') as arquivo:
        leitor = csv.reader(arquivo, delimiter=';', strict=True)
        cabecalho = next(leitor, None)
        if cabecalho is None:
            raise ValueError('O arquivo CSV está vazio.')

        colunas_vazias_finais = 0
        for coluna in reversed(cabecalho):
            if coluna != '':
                break
            colunas_vazias_finais += 1

        indice_descricao = (
            3 if len(cabecalho) == 30
            and cabecalho[:4] == ['Código', 'Produto', 'Plaqueta', 'Descrição']
            else None
        )
        colunas_apos_descricao = (
            len(cabecalho) - indice_descricao - 1
            if indice_descricao is not None else None
        )
        linhas_invalidas = []
        linhas_normalizadas = [cabecalho]
        for linha in leitor:
            if not linha:
                continue

            if len(linha) > len(cabecalho) and indice_descricao is not None:
                # The SH3 export may leave semicolons in descriptions unquoted.
                inicio_colunas_finais = len(linha) - colunas_apos_descricao
                if inicio_colunas_finais > indice_descricao:
                    linha = (
                        linha[:indice_descricao]
                        + [';'.join(linha[indice_descricao:inicio_colunas_finais])]
                        + linha[inicio_colunas_finais:]
                    )

            colunas_minimas = len(cabecalho) - colunas_vazias_finais
            if len(linha) < colunas_minimas or len(linha) > len(cabecalho):
                linhas_invalidas.append(
                    (leitor.line_num, len(linha)))
                continue

            linha.extend([''] * (len(cabecalho) - len(linha)))
            linhas_normalizadas.append(linha)

    if linhas_invalidas:
        linhas = ', '.join(
            f'{numero} ({quantidade} colunas)'
            for numero, quantidade in linhas_invalidas
        )
        raise ValueError(
            'Quantidade de colunas diferente do cabeçalho nas linhas: '
            f'{linhas}. Esperadas {len(cabecalho)} colunas; '
            'somente colunas vazias no final podem ser omitidas.'
        )

    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=';', lineterminator='\n')
    escritor.writerows(linhas_normalizadas)
    buffer.seek(0)
    return pd.read_csv(buffer, sep=';', on_bad_lines='error')


def importar_csv_entrada(path_origem):
    """Valida e copia o CSV escolhido para o caminho oficial da aplicação."""
    dados = carregar_csv_entrada(path_origem)
    validar_colunas(dados)

    garantir_pastas()
    destino = DADOS_ENTRADA_DIR / NOME_ARQUIVO_ENTRADA
    temporario = destino.with_suffix('.csv.tmp')
    shutil.copy2(path_origem, temporario)
    os.replace(temporario, destino)
    return str(destino), len(dados)


def carregar_bens_alienados(path=ARQUIVO_ALIENADOS):
    """Carrega a exportação de movimentações de alienação como itens conferíveis."""
    if not os.path.exists(path):
        return pd.DataFrame()

    bruto = pd.read_excel(path, header=None, dtype=str).fillna('')
    linhas_cabecalho = bruto.apply(
        lambda linha: linha.astype(str).str.strip().eq('Plaqueta').any(), axis=1
    )
    if not linhas_cabecalho.any():
        raise ValueError('Cabeçalho não encontrado em Bens_Alienados.xlsx')
    indice_cabecalho = linhas_cabecalho[linhas_cabecalho].index[0]
    dados = bruto.iloc[indice_cabecalho + 1:].copy()
    dados.columns = bruto.iloc[indice_cabecalho].tolist()
    dados = dados.loc[:, ~dados.columns.duplicated()]
    dados = dados.reset_index(drop=True)
    obrigatorias = ['Plaqueta', 'Bem / Descrição', 'Data']
    faltantes = [
        coluna for coluna in obrigatorias if coluna not in dados.columns]
    if faltantes:
        raise ValueError(
            f"Colunas obrigatórias ausentes em Bens_Alienados.xlsx: {', '.join(faltantes)}"
        )
    dados = dados[dados['Bem / Descrição']
                  .astype(str).str.strip().ne('')].copy()

    alienados = pd.DataFrame({
        'Código': '',
        'Plaqueta': dados['Plaqueta'],
        'Descrição': dados['Bem / Descrição'],
        'Modelo': '',
        'Setor Atual': 'Alienados',
        'Data': dados['Data'],
    })
    for coluna in COLUNAS_PATRIMONIAIS:
        alienados[coluna] = ''

    alienados = formatar_plaqueta(alienados)
    alienados = marcar_duplicidades(alienados)
    alienados['STATUS'] = 'ALIEN'
    alienados['ESTADO'] = 'NAO_APLICAVEL'
    alienados['OBSERVACAO'] = 'BEM ALIENADO'
    alienados['ALTERADO_EM'] = ''
    return alienados


def incluir_bens_alienados(df_base, path=ARQUIVO_ALIENADOS):
    """Sincroniza alienados, atualizando itens existentes e incluindo os novos."""
    alienados = carregar_bens_alienados(path)
    if alienados.empty:
        return df_base

    base = df_base.copy()
    plaquetas_base = base['Plaqueta'].map(formatar_plaqueta_exibicao)
    alienados_por_plaqueta = alienados.drop_duplicates(
        'Plaqueta', keep='last').set_index('Plaqueta')
    colunas_atualizaveis = ['Descrição', 'Setor Atual',
                            'STATUS', 'ESTADO', 'OBSERVACAO']

    for indice in base.index[plaquetas_base.isin(alienados_por_plaqueta.index)]:
        plaqueta = plaquetas_base.loc[indice]
        origem = alienados_por_plaqueta.loc[plaqueta]
        for coluna in colunas_atualizaveis:
            base.at[indice, coluna] = origem[coluna]

    existentes = set(plaquetas_base[plaquetas_base.ne('')])
    novos = alienados[~alienados['Plaqueta'].isin(existentes)]
    if novos.empty:
        return base

    for coluna in base.columns:
        if coluna not in novos.columns:
            novos[coluna] = ''
    for coluna in novos.columns:
        if coluna not in base.columns:
            base[coluna] = ''
    return pd.concat([base, novos[base.columns]], ignore_index=True)


def carregar_bens_baixados(path=ARQUIVO_BAIXADOS):
    """Carrega plaquetas da relação Excel de bens baixados."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=['Plaqueta'])

    try:
        bruto = pd.read_excel(path, header=None, dtype=object).fillna('')
    except (OSError, ValueError, ImportError, zipfile.BadZipFile) as error:
        raise ValueError(f'Não foi possível ler Bens_Baixados.xlsx: {error}') from error

    cabecalho = None
    coluna_plaqueta = None
    for indice, linha in bruto.iterrows():
        coluna = next(
            (
                posicao for posicao, valor in enumerate(linha)
                if str(valor).strip().casefold() == 'plaqueta'
            ),
            None,
        )
        if coluna is not None:
            cabecalho = indice
            coluna_plaqueta = coluna
            break
    if cabecalho is None or coluna_plaqueta is None:
        raise ValueError(
            'Coluna obrigatória "Plaqueta" não encontrada em Bens_Baixados.xlsx.'
        )

    valores = bruto.iloc[cabecalho + 1:, coluna_plaqueta]
    plaquetas = valores.map(formatar_plaqueta_exibicao)
    plaquetas = plaquetas[plaquetas.ne('')].drop_duplicates()
    if plaquetas.empty:
        raise ValueError('Bens_Baixados.xlsx não contém plaquetas válidas.')
    return pd.DataFrame({'Plaqueta': plaquetas.reset_index(drop=True)})


def incluir_bens_baixados(df_base, path=ARQUIVO_BAIXADOS):
    """Marca como baixados os bens do cadastro listados na planilha oficial."""
    baixados = carregar_bens_baixados(path)
    if baixados.empty:
        return df_base

    base = df_base.copy()
    if 'STATUS' not in base.columns:
        base['STATUS'] = ''
    if 'ESTADO' not in base.columns:
        base['ESTADO'] = ''

    plaquetas_baixadas = set(baixados['Plaqueta'])
    plaquetas_base = base['Plaqueta'].map(formatar_plaqueta_exibicao)
    mascara_baixados = plaquetas_base.isin(plaquetas_baixadas)
    base.loc[mascara_baixados, 'STATUS'] = 'BAIXA'
    base.loc[mascara_baixados, 'ESTADO'] = 'NAO_APLICAVEL'
    return base


def validar_colunas(df):
    faltantes = [col for col in COLUNAS_OBRIGATORIAS if col not in df.columns]
    if faltantes:
        raise ValueError(
            f"Colunas obrigatórias ausentes: {', '.join(faltantes)}")
    return True


def carregar_setores_excluidos():
    if not os.path.exists(ARQUIVO_SETORES_EXCLUIDOS):
        return set()

    try:
        with open(ARQUIVO_SETORES_EXCLUIDOS, 'r', encoding='utf-8') as arquivo:
            setores = json.load(arquivo)
        return {str(setor).strip() for setor in setores if str(setor).strip()}
    except (OSError, json.JSONDecodeError, TypeError):
        return set()


def registrar_setor_excluido(setor):
    setor = str(setor).strip()
    if not setor:
        return

    setores = carregar_setores_excluidos()
    setores.add(setor)
    garantir_pastas()
    with open(ARQUIVO_SETORES_EXCLUIDOS, 'w', encoding='utf-8') as arquivo:
        json.dump(sorted(setores), arquivo, ensure_ascii=False, indent=2)


def remover_setores_excluidos(df):
    setores_excluidos = carregar_setores_excluidos()
    if not setores_excluidos or 'Setor Atual' not in df.columns:
        return df

    setores = df['Setor Atual'].fillna('').astype(str).str.strip()
    return df.loc[~setores.isin(setores_excluidos)].copy()


def preparar_operacional(df):
    validar_colunas(df)
    colunas_saida = COLUNAS_OBRIGATORIAS + COLUNAS_PATRIMONIAIS
    for coluna in colunas_saida:
        if coluna not in df.columns:
            df[coluna] = ''
    df_operacional = df[colunas_saida].copy()
    df_operacional = formatar_plaqueta(df_operacional)
    df_operacional = marcar_duplicidades(df_operacional)
    df_operacional['STATUS'] = 'PEND'
    df_operacional['ESTADO'] = ''
    df_operacional['OBSERVACAO'] = ''
    df_operacional['ALTERADO_EM'] = ''
    return df_operacional


def incluir_itens_sem_plaqueta(df_base, path=ARQUIVO_ITENS_SEM_PLAQUETAS):
    """Acrescenta os achados físicos da planilha operacional à conferência."""
    itens = converter_para_conferencia(carregar_itens_sem_plaqueta(path))
    if itens.empty:
        return df_base

    base = df_base.copy()
    for coluna in itens.columns:
        if coluna not in base.columns:
            base[coluna] = ''
    for coluna in base.columns:
        if coluna not in itens.columns:
            itens[coluna] = ''

    return pd.concat([base, itens[base.columns]], ignore_index=True)


def salvar_planilha(df, path=ARQUIVO_SAIDA):
    garantir_pastas()
    df.to_excel(path, index=False)


def mesclar_alteracoes(df_base):
    """
    Mescla as alterações do arquivo de resultado com os dados base.
    Preserva TODOS os itens do arquivo de resultado, mesmo os que não estão no CSV original.
    """
    # Se não existe arquivo de resultado, retorna os dados base
    if not os.path.exists(ARQUIVO_RESULTADO):
        return df_base

    try:
        # Carregar o arquivo de resultado
        resultado_df = pd.read_excel(ARQUIVO_RESULTADO)
        resultado_df = resultado_df.fillna('')
        resultado_df = formatar_plaqueta(resultado_df)
    except Exception as error:
        raise ValueError(
            'A planilha atual de conferência está inválida; '
            'a preparação foi interrompida para preservar os dados.'
        ) from error

    backup_df = pd.DataFrame()
    if os.path.exists(ARQUIVO_BACKUP_HISTORICO):
        try:
            backup_df = pd.read_excel(ARQUIVO_BACKUP_HISTORICO).fillna('')
            backup_df = formatar_plaqueta(backup_df)
        except Exception as error:
            raise ValueError(
                'O backup de conferência usado na preparação está inválido.'
            ) from error

    # Garantir colunas necessárias no resultado
    for col in ['STATUS', 'ESTADO', 'OBSERVACAO', 'ALTERADO_EM'] + COLUNAS_HISTORICO_ALIENACAO:
        if col not in resultado_df.columns:
            resultado_df[col] = ''

    # Colunas que devem ser preservadas do arquivo de resultado
    preserve_cols = ['STATUS', 'ESTADO', 'OBSERVACAO', 'ALTERADO_EM']

    # Criar índices para busca eficiente no resultado
    def criar_indice(df, coluna):
        if coluna not in df.columns:
            return {}
        indice = {}
        for idx, linha in df.iterrows():
            chave = str(linha.get(coluna, '')).strip()
            if chave and chave not in indice:
                indice[chave] = idx
        return indice

    indice_chave_resultado = criar_indice(
        resultado_df, COLUNA_CHAVE_CONFERENCIA)
    indice_codigo_resultado = criar_indice(resultado_df, 'Código')
    indice_plaqueta_resultado = criar_indice(resultado_df, 'Plaqueta')
    indice_plaqueta_backup = criar_indice(backup_df, 'Plaqueta')

    # Conjuntos para controle de itens já processados
    codigos_processados = set()
    plaquetas_processadas = set()
    linhas_finais = []

    # Função para encontrar linha no resultado
    def encontrar_linha_resultado(codigo, plaqueta, identificador_provisorio=''):
        codigo = str(codigo).strip()
        plaqueta = formatar_plaqueta_exibicao(plaqueta)
        chave = criar_chave_conferencia(
            codigo, plaqueta, identificador_provisorio)

        if chave in indice_chave_resultado:
            return indice_chave_resultado[chave]
        if codigo in indice_codigo_resultado:
            return indice_codigo_resultado[codigo]
        if plaqueta in indice_plaqueta_resultado:
            return indice_plaqueta_resultado[plaqueta]
        return None

    # PASSO 1: Processar itens do BASE
    for _, row in df_base.iterrows():
        codigo = str(row.get('Código', '')).strip()
        plaqueta = formatar_plaqueta_exibicao(row.get('Plaqueta', ''))
        identificador_provisorio = row.get(COLUNA_IDENTIFICADOR_PROVISORIO, '')

        idx_resultado = encontrar_linha_resultado(
            codigo, plaqueta, identificador_provisorio)

        if idx_resultado is not None:
            # Item está no resultado - usar dados do resultado
            linha = resultado_df.loc[idx_resultado].copy()

            # Converter para dicionário e depois para Series para evitar problemas de tipo
            linha_dict = linha.to_dict()

            item_alienado = str(row.get('Setor Atual', '')
                                ).strip() == 'Alienados'
            historico_vazio = not any(
                str(linha_dict.get(coluna, '')).strip()
                for coluna in COLUNAS_HISTORICO_ALIENACAO
            )
            status_anterior = str(linha_dict.get('STATUS', '')).strip().upper()
            if item_alienado and historico_vazio and status_anterior != 'ALIEN':
                linha_dict.update({
                    'STATUS_ANTERIOR': linha_dict.get('STATUS', ''),
                    'SETOR_ANTERIOR': linha_dict.get('Setor Atual', ''),
                    'OBSERVACAO_ANTERIOR': linha_dict.get('OBSERVACAO', ''),
                    'DATA_SINCRONIZACAO': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                })
            elif item_alienado and historico_vazio and status_anterior == 'ALIEN':
                indice_backup = indice_plaqueta_backup.get(plaqueta)
                if indice_backup is not None:
                    linha_anterior = backup_df.loc[indice_backup]
                    linha_dict.update({
                        'STATUS_ANTERIOR': linha_anterior.get('STATUS', ''),
                        'SETOR_ANTERIOR': linha_anterior.get('Setor Atual', ''),
                        'OBSERVACAO_ANTERIOR': linha_anterior.get('OBSERVACAO', ''),
                        'DATA_SINCRONIZACAO': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                    })

            # Atualizar com dados do base (exceto colunas preservadas)
            for coluna in df_base.columns:
                if coluna not in preserve_cols:
                    valor_base = row.get(coluna, '')
                    if pd.notna(valor_base) and str(valor_base).strip():
                        linha_dict[coluna] = valor_base
                elif coluna not in linha_dict:
                    linha_dict[coluna] = row.get(coluna, '')

            linhas_finais.append(linha_dict)
        else:
            # Item não está no resultado - usar dados do base
            linhas_finais.append(row.to_dict())

        codigos_processados.add(codigo)
        if plaqueta:
            plaquetas_processadas.add(plaqueta)

    # PASSO 2: Adicionar itens do RESULTADO que não estão no BASE
    # (itens incluídos manualmente, como o ônibus com plaqueta 003488)
    for idx, row in resultado_df.iterrows():
        codigo = str(row.get('Código', '')).strip()
        plaqueta = formatar_plaqueta_exibicao(row.get('Plaqueta', ''))
        identificador_provisorio = row.get(COLUNA_IDENTIFICADOR_PROVISORIO, '')
        descricao = str(row.get('Descrição', '')).strip()

        # Verificar se já foi processado
        ja_processado = False

        if codigo and codigo in codigos_processados:
            ja_processado = True
        elif plaqueta and plaqueta in plaquetas_processadas:
            ja_processado = True
        else:
            # Verificar por chave
            chave = criar_chave_conferencia(
                codigo, plaqueta, identificador_provisorio)
            for linha_dict in linhas_finais:
                chave_linha = criar_chave_conferencia(
                    linha_dict.get('Código', ''),
                    linha_dict.get('Plaqueta', ''),
                    linha_dict.get(COLUNA_IDENTIFICADOR_PROVISORIO, '')
                )
                if chave_linha == chave:
                    ja_processado = True
                    break

        # Adicionar se não foi processado e tem descrição ou plaqueta
        if not ja_processado and (descricao or plaqueta):
            linhas_finais.append(row.to_dict())
            if codigo:
                codigos_processados.add(codigo)
            if plaqueta:
                plaquetas_processadas.add(plaqueta)

    # Criar DataFrame final
    df_final = pd.DataFrame(linhas_finais)

    # Garantir colunas necessárias
    for col in ['STATUS', 'ESTADO', 'OBSERVACAO', 'ALTERADO_EM'] + COLUNAS_HISTORICO_ALIENACAO:
        if col not in df_final.columns:
            df_final[col] = ''

    # Reformatar plaquetas e marcar duplicidades
    df_final = formatar_plaqueta(df_final)
    df_final = marcar_duplicidades(df_final)

    return df_final


def preparar_sistema_refinado():
    garantir_pastas()
    df = carregar_csv_entrada()
    df_operacional = preparar_operacional(df)
    df_operacional = incluir_itens_sem_plaqueta(df_operacional)
    df_operacional = incluir_bens_alienados(df_operacional)
    df_operacional = mesclar_alteracoes(df_operacional)
    df_operacional = remover_setores_excluidos(df_operacional)
    df_operacional = incluir_bens_baixados(df_operacional)

    alienados = carregar_bens_alienados()
    if not alienados.empty:
        plaquetas_alienadas = set(
            alienados['Plaqueta'].astype(str).str.strip())
        mascara_alienados = df_operacional['Plaqueta'].astype(
            str).str.strip().isin(plaquetas_alienadas)
        df_operacional.loc[mascara_alienados, 'Setor Atual'] = 'Alienados'
        df_operacional.loc[mascara_alienados, 'STATUS'] = 'ALIEN'
        df_operacional.loc[mascara_alienados, 'ESTADO'] = 'NAO_APLICAVEL'
        df_operacional.loc[mascara_alienados, 'OBSERVACAO'] = 'BEM ALIENADO'

        linha_totalizacao = (
            df_operacional['Plaqueta'].astype(str).str.strip().str.upper().eq('TOTALGERAL') &
            df_operacional['Descrição'].astype(str).str.strip().eq('')
        )
        df_operacional = df_operacional.loc[~linha_totalizacao].copy()

    salvar_planilha(df_operacional)
    if os.path.exists(ARQUIVO_RESULTADO):
        salvar_planilha(df_operacional, ARQUIVO_RESULTADO)
        criar_versao_resultado()
    return len(df_operacional)


if __name__ == '__main__':
    try:
        total = preparar_sistema_refinado()
        print(f"{total} bens preparados em {ARQUIVO_SAIDA}.")
    except Exception as error:
        print(f"Erro ao preparar dados: {error}")
