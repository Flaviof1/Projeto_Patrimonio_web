import getpass
import os
import socket

from flask import Flask, jsonify
from secrets import token_hex
from werkzeug.exceptions import HTTPException

from paths import garantir_pastas
from mysql_store import mysql_ativo
from rotas_operacoes import blueprint as operacoes_blueprint
from rotas_patrimonio import blueprint as patrimonio_blueprint
from rotas_leilao import blueprint as leilao_blueprint

MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def create_app():
    garantir_pastas()
    aplicacao = Flask(__name__)
    aplicacao.secret_key = token_hex(32)
    aplicacao.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES

    @aplicacao.errorhandler(HTTPException)
    def erro_http(error):
        return jsonify(error=error.description), error.code

    @aplicacao.errorhandler(Exception)
    def erro_inesperado(error):
        aplicacao.logger.exception('Erro n\u00e3o tratado na aplica\u00e7\u00e3o local')
        return jsonify(error=f'Falha inesperada: {error}'), 500

    aplicacao.register_blueprint(operacoes_blueprint)
    aplicacao.register_blueprint(patrimonio_blueprint)
    aplicacao.register_blueprint(leilao_blueprint)
    return aplicacao


app = create_app()


if __name__ == '__main__':
    host = os.environ.get('PATRIMONIO_WEB_HOST', '0.0.0.0')
    port = 8765
    if mysql_ativo() and not os.environ.get('PATRIMONIO_MYSQL_PASSWORD'):
        os.environ['PATRIMONIO_MYSQL_PASSWORD'] = getpass.getpass(
            'Senha MySQL do patrimonio_app (nao sera exibida): '
        )
    print('Acesso neste computador: http://127.0.0.1:8765')
    if host == '0.0.0.0':
        print('Acesso pela rede local (sem autentica\u00e7\u00e3o; use somente em rede privada):')
        try:
            enderecos = sorted({
                info[4][0]
                for info in socket.getaddrinfo(
                    socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
                )
                if not info[4][0].startswith('127.')
            })
        except OSError:
            enderecos = []
        if enderecos:
            for endereco in enderecos:
                print(f'  http://{endereco}:{port}')
        else:
            print('  N\u00e3o foi poss\u00edvel detectar o IPv4; consulte "ipconfig".')
    app.run(host=host, port=port, debug=False, threaded=False)
