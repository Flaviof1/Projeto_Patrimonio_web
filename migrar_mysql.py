import argparse
import getpass
import json
import os
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


def migrar():
    from app import _criar_backup_web

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
    argparse.ArgumentParser(
        description='Migra a planilha de conferência para o banco MySQL configurado.'
    ).parse_args()
    if not os.environ.get('PATRIMONIO_MYSQL_PASSWORD'):
        os.environ['PATRIMONIO_MYSQL_PASSWORD'] = getpass.getpass(
            'Senha do usuario patrimonio_app (nao sera exibida): '
        )
    try:
        migrar()
    except (OSError, ValueError, RuntimeError, pd.errors.ParserError) as error:
        raise SystemExit(f'Migração não realizada: {error}') from error


if __name__ == '__main__':
    main()
