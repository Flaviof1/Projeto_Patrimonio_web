import getpass
import os
import tempfile
from pathlib import Path


MYSQL_CONFIG = Path(r'C:\ProgramData\MySQL\MySQL Server 8.0\my.ini')


def main():
    if os.name != 'nt':
        raise SystemExit('Este procedimento é específico para Windows.')
    if not MYSQL_CONFIG.is_file():
        raise SystemExit(f'Arquivo de configuração não encontrado: {MYSQL_CONFIG}')

    senha = getpass.getpass('Nova senha para root (não será exibida): ')
    confirmacao = getpass.getpass('Digite a nova senha novamente: ')
    if not senha or senha != confirmacao:
        raise SystemExit('Senhas vazias ou diferentes; nenhum arquivo foi criado.')
    if any(ord(caractere) < 32 for caractere in senha):
        raise SystemExit('A senha não pode conter caracteres de controle.')

    senha_sql = senha.replace("'", "''")
    conteudo = (
        "ALTER USER 'root'@'localhost' IDENTIFIED BY '"
        + senha_sql
        + "';\n"
    )
    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='ascii',
        newline='\n',
        prefix='mysql_reset_',
        suffix='.sql',
        dir=Path(tempfile.gettempdir()),
        delete=False,
    ) as arquivo:
        arquivo.write(conteudo)

    print('Arquivo temporário criado. Ele contém a senha em texto puro:')
    print(arquivo.name)
    print('Use-o apenas no mysqld local e apague-o logo após reiniciar o serviço.')


if __name__ == '__main__':
    main()
