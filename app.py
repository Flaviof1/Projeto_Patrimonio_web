import getpass
import os
import socket
import sys
from datetime import timedelta

from flask import Flask, abort, g, jsonify, redirect, request, session, url_for
from secrets import token_hex
from werkzeug.exceptions import HTTPException

from autenticacao import (
    criar_administrador_inicial,
    inicializar_armazenamento_autenticacao,
    obter_usuario,
)
from paths import DATA_DIR, garantir_pastas
from mysql_store import mysql_ativo
from rotas_autenticacao import blueprint as autenticacao_blueprint
from rotas_operacoes import blueprint as operacoes_blueprint
from rotas_patrimonio import blueprint as patrimonio_blueprint
from rotas_leilao import blueprint as leilao_blueprint

MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def _carregar_chave_sessao():
    configurada = os.environ.get('PATRIMONIO_WEB_SECRET', '').strip()
    if configurada:
        if len(configurada) < 32:
            raise RuntimeError('PATRIMONIO_WEB_SECRET deve ter pelo menos 32 caracteres.')
        return configurada

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    arquivo = DATA_DIR / '.chave_sessao'
    try:
        with arquivo.open('x', encoding='utf-8') as destino:
            destino.write(token_hex(32))
    except FileExistsError:
        pass
    try:
        chave = arquivo.read_text(encoding='utf-8').strip()
    except OSError as error:
        raise RuntimeError(
            'Não foi possível carregar a chave persistente das sessões.'
        ) from error
    if len(chave) < 32:
        raise RuntimeError('A chave persistente das sessões está inválida.')
    return chave


def create_app():
    garantir_pastas()
    inicializar_armazenamento_autenticacao()
    aplicacao = Flask(__name__)
    aplicacao.secret_key = _carregar_chave_sessao()
    aplicacao.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES
    aplicacao.config['SESSION_COOKIE_HTTPONLY'] = True
    aplicacao.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    aplicacao.config['SESSION_COOKIE_SECURE'] = (
        os.environ.get('PATRIMONIO_WEB_COOKIE_SECURE', '').strip().lower()
        in {'1', 'true', 'yes', 'sim'}
    )
    aplicacao.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
    aplicacao.config['SESSION_REFRESH_EACH_REQUEST'] = False

    @aplicacao.errorhandler(HTTPException)
    def erro_http(error):
        return jsonify(error=error.description), error.code

    @aplicacao.errorhandler(Exception)
    def erro_inesperado(error):
        aplicacao.logger.exception('Erro não tratado na aplicação web')
        return jsonify(error='Falha inesperada na aplicação.'), 500

    @aplicacao.before_request
    def exigir_autenticacao():
        if request.endpoint in {'static', 'autenticacao.login'}:
            return None

        usuario = obter_usuario(session.get('usuario_id'))
        if not usuario or not usuario['ativo']:
            session.clear()
            if request.path.startswith('/api/'):
                abort(401, description='Sua sessão expirou. Faça login novamente.')
            return redirect(url_for('autenticacao.login'))

        g.usuario_atual = usuario
        if (
            usuario['trocar_senha']
            and request.endpoint not in {
                'autenticacao.trocar_senha',
                'autenticacao.logout',
            }
        ):
            if request.path.startswith('/api/'):
                abort(403, description='Troque sua senha antes de continuar.')
            return redirect(url_for('autenticacao.trocar_senha'))
        return None

    aplicacao.register_blueprint(autenticacao_blueprint)
    aplicacao.register_blueprint(operacoes_blueprint)
    aplicacao.register_blueprint(patrimonio_blueprint)
    aplicacao.register_blueprint(leilao_blueprint)
    return aplicacao


app = create_app()


if __name__ == '__main__':
    if '--criar-admin' in sys.argv:
        try:
            administrador = criar_administrador_inicial()
        except ValueError as error:
            print(f'Não foi possível criar o administrador: {error}', file=sys.stderr)
            raise SystemExit(1)
        print(f'Conta administradora "{administrador["nome_usuario"]}" criada.')
        raise SystemExit(0)

    host = os.environ.get('PATRIMONIO_WEB_HOST', '0.0.0.0')
    port = 8765
    if mysql_ativo() and not os.environ.get('PATRIMONIO_MYSQL_PASSWORD'):
        os.environ['PATRIMONIO_MYSQL_PASSWORD'] = getpass.getpass(
            'Senha MySQL do patrimonio_app (nao sera exibida): '
        )
    print('Acesso neste computador: http://127.0.0.1:8765')
    if host == '0.0.0.0':
        print('Acesso pela rede local (login obrigatório; não exponha sem HTTPS):')
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
