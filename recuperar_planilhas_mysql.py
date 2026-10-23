import getpass
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from mysql_store import MySQLStore
from paths import (
    ARQUIVO_CAMPOS,
    ARQUIVO_RESULTADO,
    DATA_DIR,
    DADOS_ENTRADA_DIR,
    DADOS_TRABALHO_DIR,
    FOTOS_DIR,
)


EXPECTED_RECORDS = 6432
REQUIRED_COLUMNS = {'Plaqueta', 'Descrição', 'Setor Atual', 'STATUS'}
SNAPSHOT_FOLDERS = ('dados_entrada', 'dados_trabalho', 'backups', 'relatorios')


def _snapshot_atual():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    destino = DATA_DIR / f'recuperacao_mysql_{timestamp}'
    destino.mkdir(parents=True, exist_ok=False)
    for nome in SNAPSHOT_FOLDERS:
        atual = DATA_DIR / nome
        if atual.is_dir():
            shutil.copytree(atual, destino / nome)
    return destino


def _escrever_planilhas(dados, snapshot):
    destinos = (Path(ARQUIVO_RESULTADO), Path(ARQUIVO_CAMPOS))
    DADOS_TRABALHO_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix='recuperacao_mysql_', dir=DADOS_TRABALHO_DIR
    ) as temporario:
        pasta_temporaria = Path(temporario)
        staged = []
        for destino in destinos:
            arquivo_temporario = pasta_temporaria / destino.name
            dados.to_excel(arquivo_temporario, index=False)
            conferida = pd.read_excel(arquivo_temporario, dtype=object).fillna('')
            if len(conferida) != len(dados) or set(conferida.columns) != set(dados.columns):
                raise ValueError(
                    f'A verificacao previa de {destino.name} falhou; '
                    'nenhuma planilha foi substituida.'
                )
            staged.append((arquivo_temporario, destino))

        instalados = []
        try:
            for temporario, destino in staged:
                os.replace(temporario, destino)
                instalados.append(destino)
        except OSError:
            for destino in reversed(instalados):
                preservado = snapshot / 'dados_trabalho' / destino.name
                if preservado.is_file():
                    shutil.copy2(preservado, destino)
                else:
                    destino.unlink(missing_ok=True)
            raise


def main():
    os.environ['PATRIMONIO_MYSQL_PASSWORD'] = getpass.getpass(
        'Senha do usuario patrimonio_app (nao sera exibida): '
    )
    banco = MySQLStore.from_environment()
    dados = banco.load()
    if dados is None:
        raise SystemExit('O banco MySQL nao retornou registros; nada foi alterado.')
    if len(dados) != EXPECTED_RECORDS:
        raise SystemExit(
            f'Esperados {EXPECTED_RECORDS} registros, encontrados {len(dados)}; '
            'nada foi alterado.'
        )
    ausentes = REQUIRED_COLUMNS.difference(dados.columns)
    if ausentes:
        raise SystemExit(
            'Colunas obrigatorias ausentes no MySQL: '
            + ', '.join(sorted(ausentes))
            + '; nada foi alterado.'
        )
    if not dados.index.is_unique or dados.index.min() != 0 or dados.index.max() != len(dados) - 1:
        raise SystemExit(
            'Os identificadores dos registros nao formam a sequencia esperada; '
            'nada foi alterado.'
        )

    fotos_referenciadas = (
        int(dados['Foto'].fillna('').astype(str).str.strip().ne('').sum())
        if 'Foto' in dados.columns
        else 0
    )
    FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    fotos_disponiveis = sum(1 for path in FOTOS_DIR.iterdir() if path.is_file())
    snapshot = _snapshot_atual()
    _escrever_planilhas(dados.reset_index(drop=True), snapshot)

    for destino in (Path(ARQUIVO_RESULTADO), Path(ARQUIVO_CAMPOS)):
        conferida = pd.read_excel(destino, dtype=object).fillna('')
        if len(conferida) != EXPECTED_RECORDS or set(conferida.columns) != set(dados.columns):
            raise SystemExit(
                f'A verificacao de {destino.name} falhou; '
                f'os arquivos anteriores estao preservados em {snapshot}.'
            )

    print(f'Planilhas reconstruidas e verificadas: {len(dados)} registros cada.')
    print(f'Copia de seguranca do estado anterior: {snapshot}')
    print(
        f'Referencias de foto preservadas nos dados: {fotos_referenciadas}; '
        f'arquivos de imagem atualmente encontrados: {fotos_disponiveis}.'
    )
    print(
        f'CSV de entrada preservado como encontrado: '
        f'{(DADOS_ENTRADA_DIR / "Localizar_Bens.csv").is_file()}.'
    )


if __name__ == '__main__':
    main()
