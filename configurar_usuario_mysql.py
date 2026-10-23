import getpass
import re

from mysql_store import MySQLStore


def main():
    banco = MySQLStore.from_environment()
    senha_root = getpass.getpass('Senha root do MySQL (nao sera exibida): ')
    senha_app = getpass.getpass('Nova senha do usuario patrimonio_app: ')
    confirmacao = getpass.getpass('Confirme a nova senha do patrimonio_app: ')
    if not senha_app or senha_app != confirmacao:
        raise SystemExit('Senhas vazias ou diferentes; nenhuma alteracao realizada.')

    connector = MySQLStore._connector()
    try:
        admin = connector.connect(
            host=banco.host,
            port=banco.port,
            user='root',
            password=senha_root,
            connection_timeout=5,
            autocommit=True,
        )
    except connector.Error as error:
        raise SystemExit(f'Falha ao autenticar como root: {error}') from error

    if not re.fullmatch(r'[A-Za-z0-9_]+', banco.database):
        admin.close()
        raise SystemExit('O nome do banco MySQL contem caracteres nao permitidos.')

    try:
        cursor = admin.cursor()
        try:
            cursor.execute(
                f'CREATE DATABASE IF NOT EXISTS `{banco.database}` '
                'CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci'
            )
            cursor.execute(
                "SELECT COUNT(*) FROM mysql.user "
                "WHERE User = 'patrimonio_app' AND Host = '127.0.0.1'"
            )
            conta_existe = cursor.fetchone()[0] > 0
            if conta_existe:
                cursor.execute(
                    "ALTER USER 'patrimonio_app'@'127.0.0.1' IDENTIFIED BY %s",
                    (senha_app,),
                )
            else:
                cursor.execute(
                    "CREATE USER 'patrimonio_app'@'127.0.0.1' IDENTIFIED BY %s",
                    (senha_app,),
                )
            cursor.execute(
                f'GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX '
                f'ON `{banco.database}`.* TO '
                "'patrimonio_app'@'127.0.0.1'"
            )
        except connector.Error as error:
            raise SystemExit(
                'Nao foi possivel configurar a conta local patrimonio_app. '
                f'Detalhe MySQL: {error}'
            ) from error
        finally:
            cursor.close()
    finally:
        admin.close()

    try:
        validacao = connector.connect(
            host=banco.host,
            port=banco.port,
            database=banco.database,
            user=banco.user,
            password=senha_app,
            connection_timeout=5,
        )
    except connector.Error as error:
        raise SystemExit(
            'A senha foi alterada, mas o teste de acesso do aplicativo falhou. '
            f'Detalhe MySQL: {error}'
        ) from error
    else:
        validacao.close()

    operacao = 'atualizada' if conta_existe else 'criada'
    print(
        f'Conta patrimonio_app {operacao}; permissao local configurada e '
        f'conexao com {banco.database} validada.'
    )


if __name__ == '__main__':
    main()
