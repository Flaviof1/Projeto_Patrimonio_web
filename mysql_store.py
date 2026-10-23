import json
import os
from datetime import date, datetime

import pandas as pd


TABLE_NAME = 'patrimonio_itens'
INDEXED_FIELDS = {
    'codigo': 'Código',
    'plaqueta': 'Plaqueta',
    'descricao': 'Descrição',
    'setor_atual': 'Setor Atual',
    'status': 'STATUS',
    'estado': 'ESTADO',
    'observacao': 'OBSERVACAO',
    'foto': 'Foto',
    'alterado_em': 'ALTERADO_EM',
}


def mysql_ativo():
    armazenamento = os.environ.get('PATRIMONIO_STORAGE', 'excel').strip().lower()
    if armazenamento not in {'excel', 'mysql'}:
        raise ValueError('PATRIMONIO_STORAGE deve ser "excel" ou "mysql".')
    return armazenamento == 'mysql'


class MySQLStore:
    def __init__(self, host, port, database, user, password):
        if not database or not user:
            raise ValueError(
                'Configure PATRIMONIO_MYSQL_DATABASE e PATRIMONIO_MYSQL_USER.'
            )
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password

    @classmethod
    def from_environment(cls):
        try:
            port = int(os.environ.get('PATRIMONIO_MYSQL_PORT', '3306'))
        except ValueError as error:
            raise ValueError('PATRIMONIO_MYSQL_PORT deve ser uma porta válida.') from error
        return cls(
            host=os.environ.get('PATRIMONIO_MYSQL_HOST', '127.0.0.1'),
            port=port,
            database=os.environ.get('PATRIMONIO_MYSQL_DATABASE', 'patrimonio_web'),
            user=os.environ.get('PATRIMONIO_MYSQL_USER', 'patrimonio_app'),
            password=os.environ.get('PATRIMONIO_MYSQL_PASSWORD', ''),
        )

    @staticmethod
    def _connector():
        try:
            import mysql.connector
        except ImportError as error:
            raise RuntimeError(
                'Driver MySQL ausente. Instale as dependências do projeto.'
            ) from error
        return mysql.connector

    def _connect(self):
        connector = self._connector()
        try:
            return connector.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                charset='utf8mb4',
                connection_timeout=5,
                autocommit=False,
            )
        except connector.Error as error:
            raise ValueError(
                f'Não foi possível conectar ao MySQL em {self.host}:{self.port}/'
                f'{self.database}: {error}'
            ) from error

    def _ensure_schema(self, connection):
        cursor = connection.cursor()
        try:
            cursor.execute(
                f'''
                CREATE TABLE IF NOT EXISTS `{TABLE_NAME}` (
                    `id` BIGINT UNSIGNED NOT NULL,
                    `codigo` VARCHAR(100) NULL,
                    `plaqueta` VARCHAR(100) NULL,
                    `descricao` TEXT NULL,
                    `setor_atual` VARCHAR(255) NULL,
                    `status` VARCHAR(32) NULL,
                    `estado` VARCHAR(32) NULL,
                    `observacao` TEXT NULL,
                    `foto` VARCHAR(255) NULL,
                    `alterado_em` VARCHAR(32) NULL,
                    `dados_completos` JSON NOT NULL,
                    PRIMARY KEY (`id`),
                    KEY `idx_patrimonio_plaqueta` (`plaqueta`),
                    KEY `idx_patrimonio_setor_status` (`setor_atual`, `status`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
                '''
            )
            connection.commit()
        finally:
            cursor.close()

    @staticmethod
    def _json_value(value):
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if hasattr(value, 'item'):
            value = value.item()
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, (str, int, float, bool, list, dict)):
            return value
        return str(value)

    @classmethod
    def _text_value(cls, row, field):
        value = cls._json_value(row.get(field))
        return None if value is None or value == '' else str(value)

    @classmethod
    def _rows(cls, dataframe):
        if not dataframe.index.is_unique:
            raise ValueError('A base contém identificadores internos duplicados.')
        columns = [
            'id',
            *INDEXED_FIELDS.keys(),
            'dados_completos',
        ]
        rows = []
        for index, row in dataframe.iterrows():
            try:
                item_id = int(index)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    'A base contém identificador interno incompatível com MySQL.'
                ) from error
            if item_id < 0:
                raise ValueError('O identificador interno do bem não pode ser negativo.')
            payload = {
                str(column): cls._json_value(value)
                for column, value in row.items()
            }
            valores = {
                'id': item_id,
                'dados_completos': json.dumps(
                    payload, ensure_ascii=False, allow_nan=False
                ),
            }
            valores.update({
                campo: cls._text_value(row, coluna)
                for campo, coluna in INDEXED_FIELDS.items()
            })
            rows.append(tuple(valores[column] for column in columns))
        return columns, rows

    def load(self):
        connector = self._connector()
        connection = self._connect()
        try:
            try:
                self._ensure_schema(connection)
                cursor = connection.cursor()
                try:
                    cursor.execute(
                        f'SELECT `id`, `dados_completos` FROM `{TABLE_NAME}` '
                        'ORDER BY `id`'
                    )
                    registros = cursor.fetchall()
                finally:
                    cursor.close()
            except connector.Error as error:
                raise ValueError(f'Falha ao consultar a base MySQL: {error}') from error
        finally:
            connection.close()

        if not registros:
            return None

        linhas = []
        indices = []
        for item_id, payload in registros:
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise ValueError('Há um registro inválido na tabela do MySQL.')
            indices.append(int(item_id))
            linhas.append(payload)
        dados = pd.DataFrame(linhas, index=indices).fillna('')
        dados.index.name = None
        return dados

    def replace_all(self, dataframe):
        columns, rows = self._rows(dataframe)
        nomes = ', '.join(f'`{column}`' for column in columns)
        placeholders = ', '.join(['%s'] * len(columns))
        connector = self._connector()
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            cursor = connection.cursor()
            try:
                cursor.execute(f'DELETE FROM `{TABLE_NAME}`')
                if rows:
                    cursor.executemany(
                        f'INSERT INTO `{TABLE_NAME}` ({nomes}) VALUES ({placeholders})',
                        rows,
                    )
                connection.commit()
            except Exception as error:
                connection.rollback()
                if isinstance(error, connector.Error):
                    raise ValueError(
                        f'Falha ao gravar a base MySQL: {error}'
                    ) from error
                raise
            finally:
                cursor.close()
        except connector.Error as error:
            connection.close()
            raise ValueError(f'Falha ao preparar a tabela MySQL: {error}') from error
        except Exception:
            connection.close()
            raise
        connection.close()

    def count(self):
        connector = self._connector()
        connection = self._connect()
        try:
            try:
                self._ensure_schema(connection)
                cursor = connection.cursor()
                try:
                    cursor.execute(f'SELECT COUNT(*) FROM `{TABLE_NAME}`')
                    return int(cursor.fetchone()[0])
                finally:
                    cursor.close()
            except connector.Error as error:
                raise ValueError(f'Falha ao consultar a base MySQL: {error}') from error
        finally:
            connection.close()
