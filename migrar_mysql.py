import argparse
import csv
import getpass
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from mysql_store import MySQLStore
from mysql_store import TABLE_NAME


def _arquivo_fonte():
    from paths import ARQUIVO_CAMPOS, ARQUIVO_RESULTADO

    for nome in (ARQUIVO_RESULTADO, ARQUIVO_CAMPOS):
        caminho = Path(nome)
        if caminho.is_file():
            return caminho
    raise FileNotFoundError(
        'Não encontrei conferencia_resultado.xlsx nem conferencia_campo.xlsx.'
    )


def _confirmar_migracao(total, banco):
    print(f'Origem: {total} registros da planilha de conferência.')
    print(f'Destino: MySQL {banco.database} em {banco.host}:{banco.port}.')
    return input('Digite MIGRAR para continuar: ').strip() == 'MIGRAR'


def _validar_copia(banco, esperado):
    copiado = banco.load()
    if copiado is None:
        raise ValueError('O MySQL não retornou os registros migrados.')
    _, linhas_esperadas = MySQLStore._rows(esperado)
    _, linhas_copiadas = MySQLStore._rows(copiado)
    if len(linhas_esperadas) != len(linhas_copiadas):
        raise ValueError(
            'A contagem de registros no MySQL difere da planilha de origem.'
        )
    for linha_esperada, linha_copiada in zip(linhas_esperadas, linhas_copiadas):
        campos_esperados = list(linha_esperada[:-1])
        campos_copiados = list(linha_copiada[:-1])
        try:
            dados_esperados = json.loads(linha_esperada[-1])
            dados_copiados = json.loads(linha_copiada[-1])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(
                'A verificação encontrou dados JSON inválidos no MySQL.'
            ) from error
        if campos_esperados != campos_copiados or dados_esperados != dados_copiados:
            raise ValueError(
                'A verificação encontrou conteúdo diferente entre a planilha '
                'e os registros existentes no MySQL. Nenhum registro foi sobrescrito.'
            )


def _carregar_mysql_somente_leitura(banco):
    connector = MySQLStore._connector()
    conexao = banco._connect()
    try:
        cursor = conexao.cursor()
        try:
            cursor.execute(
                f'SELECT `id`, `dados_completos` FROM `{TABLE_NAME}` ORDER BY `id`'
            )
            registros = cursor.fetchall()
        except connector.Error as error:
            raise ValueError(f'Falha ao consultar a base MySQL: {error}') from error
        finally:
            cursor.close()
    finally:
        conexao.close()

    linhas = []
    indices = []
    for identificador, conteudo in registros:
        if isinstance(conteudo, str):
            try:
                conteudo = json.loads(conteudo)
            except json.JSONDecodeError as error:
                raise ValueError('Há um registro JSON inválido na tabela MySQL.') from error
        if not isinstance(conteudo, dict):
            raise ValueError('Há um registro inválido na tabela MySQL.')
        indices.append(int(identificador))
        linhas.append(conteudo)
    dados = pd.DataFrame(linhas, index=indices).fillna('')
    dados.index.name = None
    return dados


def _valor_comparacao(valor):
    normalizado = MySQLStore._json_value(valor)
    return '' if normalizado is None else normalizado


def _plaqueta(registro):
    from preparar_dados import formatar_plaqueta_exibicao

    valor = _valor_comparacao(registro.get('Plaqueta', ''))
    return formatar_plaqueta_exibicao(valor)


def _normalizar_campo_comparacao(campo, valor):
    if campo in {'Plaqueta', 'Plaqueta Original'}:
        return _plaqueta({'Plaqueta': valor})
    if campo == 'Plaqueta Normalizada':
        from preparar_dados import normalizar_plaqueta_numerica

        return normalizar_plaqueta_numerica(_valor_comparacao(valor))
    return _valor_comparacao(valor)


def _agrupar_por_plaqueta(dados):
    grupos = defaultdict(list)
    sem_plaqueta = []
    for identificador, linha in dados.iterrows():
        registro = linha.to_dict()
        plaqueta = _plaqueta(registro)
        if plaqueta:
            grupos[plaqueta].append((identificador, registro))
        else:
            sem_plaqueta.append((identificador, registro))
    return grupos, sem_plaqueta


def _comparar_bases(excel, mysql):
    grupos_excel, sem_plaqueta_excel = _agrupar_por_plaqueta(excel)
    grupos_mysql, sem_plaqueta_mysql = _agrupar_por_plaqueta(mysql)
    plaquetas = set(grupos_excel) | set(grupos_mysql)
    divergencias = []
    contagem_campos = Counter()
    linhas_relatorio = []
    itens_somente_excel = 0
    itens_somente_mysql = 0
    plaquetas_duplicadas = 0

    for plaqueta in sorted(plaquetas):
        registros_excel = grupos_excel.get(plaqueta, [])
        registros_mysql = grupos_mysql.get(plaqueta, [])
        if len(registros_excel) > 1 or len(registros_mysql) > 1:
            plaquetas_duplicadas += 1
            linhas_relatorio.append({
                'tipo': 'plaqueta_duplicada',
                'plaqueta': plaqueta,
                'ids_excel': ', '.join(str(item[0]) for item in registros_excel),
                'ids_mysql': ', '.join(str(item[0]) for item in registros_mysql),
                'data_excel': '',
                'data_mysql': '',
                'campos_diferentes': '',
            })
            continue
        if not registros_excel:
            identificador, registro_mysql = registros_mysql[0]
            itens_somente_mysql += 1
            linhas_relatorio.append({
                'tipo': 'somente_mysql',
                'plaqueta': plaqueta,
                'ids_excel': '',
                'ids_mysql': str(identificador),
                'data_excel': '',
                'data_mysql': _valor_comparacao(registro_mysql.get('Data', '')),
                'campos_diferentes': '',
            })
            continue
        if not registros_mysql:
            identificador, registro_excel = registros_excel[0]
            itens_somente_excel += 1
            linhas_relatorio.append({
                'tipo': 'somente_excel',
                'plaqueta': plaqueta,
                'ids_excel': str(identificador),
                'ids_mysql': '',
                'data_excel': _valor_comparacao(registro_excel.get('Data', '')),
                'data_mysql': '',
                'campos_diferentes': '',
            })
            continue

        id_excel, registro_excel = registros_excel[0]
        id_mysql, registro_mysql = registros_mysql[0]
        colunas = set(registro_excel) | set(registro_mysql)
        campos = [
            str(coluna)
            for coluna in sorted(colunas, key=str)
            if _normalizar_campo_comparacao(
                str(coluna), registro_excel.get(coluna)
            ) != _normalizar_campo_comparacao(
                str(coluna), registro_mysql.get(coluna)
            )
        ]
        if campos:
            divergencias.append((plaqueta, campos))
            contagem_campos.update(campos)
            linhas_relatorio.append({
                'tipo': 'conteudo_diferente',
                'plaqueta': plaqueta,
                'ids_excel': str(id_excel),
                'ids_mysql': str(id_mysql),
                'data_excel': _valor_comparacao(registro_excel.get('Data', '')),
                'data_mysql': _valor_comparacao(registro_mysql.get('Data', '')),
                'campos_diferentes': ', '.join(campos),
            })

    for identificador, registro in sem_plaqueta_excel:
        linhas_relatorio.append({
            'tipo': 'sem_plaqueta_excel',
            'plaqueta': '',
            'ids_excel': str(identificador),
            'ids_mysql': '',
            'data_excel': _valor_comparacao(registro.get('Data', '')),
            'data_mysql': '',
            'campos_diferentes': '',
        })
    for identificador, registro in sem_plaqueta_mysql:
        linhas_relatorio.append({
            'tipo': 'sem_plaqueta_mysql',
            'plaqueta': '',
            'ids_excel': '',
            'ids_mysql': str(identificador),
            'data_excel': '',
            'data_mysql': _valor_comparacao(registro.get('Data', '')),
            'campos_diferentes': '',
        })

    resumo = {
        'registros_excel': len(excel),
        'registros_mysql': len(mysql),
        'plaquetas_correspondentes': (
            len(plaquetas) - plaquetas_duplicadas
            - itens_somente_excel - itens_somente_mysql
        ),
        'somente_excel': itens_somente_excel,
        'somente_mysql': itens_somente_mysql,
        'plaquetas_duplicadas': plaquetas_duplicadas,
        'sem_plaqueta_excel': len(sem_plaqueta_excel),
        'sem_plaqueta_mysql': len(sem_plaqueta_mysql),
        'conteudo_diferente': len(divergencias),
        'conteudo_identico': (
            len(plaquetas) - plaquetas_duplicadas
            - itens_somente_excel - itens_somente_mysql
            - len(divergencias)
        ),
        'campos_diferentes': contagem_campos,
    }
    return resumo, linhas_relatorio


def comparar():
    from paths import RELATORIOS_DIR

    banco = MySQLStore.from_environment()
    fonte = _arquivo_fonte()
    excel = pd.read_excel(fonte, dtype=object).fillna('')
    if excel.empty or 'Setor Atual' not in excel.columns:
        raise ValueError('A planilha de conferência está vazia ou sem "Setor Atual".')
    mysql = _carregar_mysql_somente_leitura(banco)
    resumo, linhas = _comparar_bases(excel, mysql)

    RELATORIOS_DIR.mkdir(parents=True, exist_ok=True)
    relatorio = RELATORIOS_DIR / (
        f'comparacao_mysql_excel_{datetime.now():%Y%m%d_%H%M%S_%f}.csv'
    )
    with relatorio.open('w', encoding='utf-8-sig', newline='') as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=(
                'tipo',
                'plaqueta',
                'ids_excel',
                'ids_mysql',
                'data_excel',
                'data_mysql',
                'campos_diferentes',
            ),
            delimiter=';',
        )
        escritor.writeheader()
        escritor.writerows(linhas)

    print('Comparação por plaqueta concluída; as bases e fotos não foram alteradas.')
    print(f"Registros no Excel: {resumo['registros_excel']}")
    print(f"Registros no MySQL: {resumo['registros_mysql']}")
    print(f"Plaquetas correspondentes (sem duplicidade): {resumo['plaquetas_correspondentes']}")
    print(f"Somente no Excel: {resumo['somente_excel']}")
    print(f"Somente no MySQL: {resumo['somente_mysql']}")
    print(f"Plaquetas duplicadas/ambíguas: {resumo['plaquetas_duplicadas']}")
    print(f"Registros sem plaqueta no Excel: {resumo['sem_plaqueta_excel']}")
    print(f"Registros sem plaqueta no MySQL: {resumo['sem_plaqueta_mysql']}")
    print(f"Plaquetas correspondentes com conteúdo diferente: {resumo['conteudo_diferente']}")
    print(f"Plaquetas correspondentes com conteúdo idêntico: {resumo['conteudo_identico']}")
    if resumo['campos_diferentes']:
        print('Campos com diferenças:')
        for campo, quantidade in sorted(resumo['campos_diferentes'].items()):
            print(f'  {campo}: {quantidade}')
    print(f'Relatório salvo em: {relatorio}')
    return resumo, relatorio


def migrar():
    banco = MySQLStore.from_environment()
    fonte = _arquivo_fonte()
    dados = pd.read_excel(fonte, dtype=object).fillna('')
    if dados.empty or 'Setor Atual' not in dados.columns:
        raise ValueError('A planilha de conferência está vazia ou sem "Setor Atual".')

    registros_existentes = banco.count()
    if registros_existentes:
        if registros_existentes != len(dados):
            raise ValueError(
                f'A tabela {TABLE_NAME} já contém {registros_existentes} registros, '
                f'mas a planilha tem {len(dados)}. Nenhum registro foi sobrescrito.'
            )
        _validar_copia(banco, dados)
        print(
            f'Migração já presente e validada: {registros_existentes} registros '
            'correspondem à planilha.'
        )
        print(f'Planilha original preservada em {fonte}.')
        return True
    if not _confirmar_migracao(len(dados), banco):
        print('Migração cancelada; nenhum registro foi alterado.')
        return False

    from servicos_operacoes import _criar_backup_web

    backup = _criar_backup_web()
    banco.replace_all(dados)
    if banco.count() != len(dados):
        raise ValueError(
            'A contagem no MySQL não corresponde à planilha. '
            'Os dados existentes foram mantidos para inspeção.'
        )
    _validar_copia(banco, dados)

    print(f'Migração concluída e validada ({len(dados)} registros).')
    print(f'Backup das planilhas e fotos: {backup}')
    print('Para ativar o MySQL, configure PATRIMONIO_STORAGE=mysql ao iniciar o app.')
    print(f'Planilha de origem preservada em {fonte}.')
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Migra a planilha de conferência para o banco MySQL configurado.'
    )
    parser.add_argument(
        '--comparar',
        action='store_true',
        help='compara Excel e MySQL por plaqueta, sem alterar os dados de origem',
    )
    argumentos = parser.parse_args()
    if not os.environ.get('PATRIMONIO_MYSQL_PASSWORD'):
        os.environ['PATRIMONIO_MYSQL_PASSWORD'] = getpass.getpass(
            'Senha do usuario patrimonio_app (nao sera exibida): '
        )
    try:
        if argumentos.comparar:
            comparar()
        else:
            migrar()
    except (OSError, ValueError, RuntimeError, pd.errors.ParserError) as error:
        raise SystemExit(f'Migração não realizada: {error}') from error


if __name__ == '__main__':
    main()
