# Gestão Patrimonial — aplicação web

Aplicação web em português para preparar e consultar o cadastro patrimonial,
conferir bens por setor, registrar fotos e observações, pesquisar itens,
acompanhar uma lista separada de bens previstos para leilão e gerar relatórios.

O aplicativo roda no computador servidor. Os demais dispositivos acessam a
interface pelo navegador, na rede local ou por uma rede privada Tailscale.
**Não há usuários, senhas ou permissões dentro do aplicativo.** Qualquer pessoa
que consiga acessar o endereço pode consultar e alterar dados, importar
arquivos e criar ou restaurar backups. Restrinja o acesso a pessoas confiáveis;
não exponha a porta na internet.

O [Manual do Sistema (HTML)](MANUAL_USUARIO.html) também descreve a operação
para quem realiza a conferência.

## Requisitos

- Windows
- Python 3.10 ou superior
- MySQL 8 se for usar o armazenamento MySQL; o modo Excel não precisa do MySQL

As bibliotecas necessárias estão listadas em `requirements.txt`.

## Instalar e iniciar

Abra o PowerShell na pasta do projeto e execute:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Por padrão, o aplicativo usa arquivos Excel como armazenamento. Para usar o
MySQL, configure o modo conforme a seção [Armazenamento](#armazenamento).

No computador servidor, abra <http://127.0.0.1:8765>. O terminal informa os
endereços disponíveis na rede local; use um deles em outro dispositivo
conectado à mesma rede. Se o Firewall do Windows solicitar autorização, permita
somente em redes privadas. Para encerrar, volte ao terminal e pressione
`Ctrl+C`.

O servidor atende uma solicitação por vez. Evite que várias pessoas editem os
mesmos dados simultaneamente.

### Acesso remoto por Tailscale

Instale e conecte o Tailscale no servidor e nos dispositivos autorizados. No
painel do Tailscale, localize o endereço IPv4 atual do computador servidor e
acesse no outro dispositivo `http://IP-DO-SERVIDOR:8765`. O Flask deve
continuar aberto e o computador não pode estar desligado ou suspenso.

O endereço pode ser consultado no painel de máquinas do Tailscale; não presuma
que um endereço de exemplo neste documento ainda esteja atualizado. Não
configure encaminhamento da porta no roteador nem exponha o Flask diretamente à
internet. O Tailscale protege a conexão de rede, mas não adiciona controle de
acesso ao sistema.

## Armazenamento

O modo padrão é `excel`. A instalação atual pode usar MySQL, configurado ao
iniciar o aplicativo com `PATRIMONIO_STORAGE=mysql`. Essa variável vale apenas
para o terminal em que foi definida.

### Usar MySQL em uma instalação já configurada

Inicie o serviço `MySQL80` no Windows e, no PowerShell, execute:

```powershell
$env:PATRIMONIO_STORAGE = "mysql"
.\.venv\Scripts\python.exe app.py
```

Se a senha MySQL não estiver configurada no ambiente, o aplicativo a solicitará
no terminal sem exibi-la. Para usar Excel em um terminal PowerShell:

```powershell
$env:PATRIMONIO_STORAGE = "excel"
.\.venv\Scripts\python.exe app.py
```

No modo MySQL, o banco ativo é `patrimonio_web`, na tabela
`patrimonio_itens`. As planilhas de conferência também são mantidas como
espelhos para preparação, relatórios e recuperação. O MySQL fica no servidor;
os dispositivos clientes acessam somente o Flask.

### Configurar uma instalação MySQL nova ou planejada

Não repita estes passos em uma instalação já configurada. No MySQL, crie um
banco e um usuário próprio:

```sql
CREATE DATABASE patrimonio_web
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'patrimonio_app'@'127.0.0.1' IDENTIFIED BY 'defina-uma-senha-forte';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX
  ON patrimonio_web.* TO 'patrimonio_app'@'127.0.0.1';
```

Configure a senha do usuário pelo utilitário, que solicita as senhas sem
exibi-las:

```powershell
.\.venv\Scripts\python.exe configurar_usuario_mysql.py
```

Com o Flask parado, execute a migração. O script pede a senha e a confirmação
`MIGRAR`, cria um backup e valida os dados copiados:

```powershell
.\.venv\Scripts\python.exe migrar_mysql.py
```

Se precisar reconstruir as planilhas espelho a partir do banco, use:

```powershell
.\.venv\Scripts\python.exe recuperar_planilhas_mysql.py
```

Essa recuperação não recria arquivos de entrada ou fotos que não estejam no
banco ou nas pastas de dados. Consulte o estado dos dados e mantenha um backup
antes de uma migração ou recuperação planejada.

## Dados e pastas

Por padrão, os arquivos ficam em `dados_web`, dentro da pasta do projeto. A
variável `PATRIMONIO_WEB_DATA_DIR` pode apontar para outra pasta. A aplicação
não lê nem importa automaticamente os dados do projeto desktop: selecione e
importe os arquivos pela interface.

- `dados_web\dados_entrada`: CSV patrimonial e arquivos auxiliares importados
- `dados_web\dados_trabalho`: planilhas de conferência, histórico, versões,
  fotos e lista de leilão
- `dados_web\relatorios`: relatórios gerados
- `dados_web\backups`: backups ZIP criados pelo aplicativo

As importações são validadas antes de instalar os arquivos. Se já houver um
arquivo de entrada do mesmo tipo, a interface pede confirmação para substituí-lo.
A preparação e a restauração criam um backup web antes de atualizar os dados.

## Fluxo de trabalho patrimonial

### 1. Importar e preparar os dados

1. Em **Importar cadastro patrimonial**, selecione o CSV exportado pelo SH3. O
   arquivo deve usar `;` como separador e conter exatamente estes cabeçalhos
   obrigatórios: `Código`, `Plaqueta`, `Descrição`, `Modelo`, `Setor Atual` e
   `Data`.
2. Opcionalmente, importe as planilhas auxiliares:
   - **Bens alienados**: Excel `.xlsx`, com colunas `Plaqueta`,
     `Bem / Descrição` e `Data`.
   - **Bens baixados**: Excel `.xlsx`, com uma coluna `Plaqueta`.
3. Clique em **Preparar Dados (SH3)**. A preparação cria ou atualiza a base de
   conferência a partir do CSV e cruza as plaquetas com as fontes auxiliares.
   Alienados recebem `ALIEN`; baixados recebem `BAIXA`. Se a mesma plaqueta
   constar nas duas fontes, `ALIEN` prevalece.
4. A preparação atualiza a base operacional. Se já houver dados, confirme a
   operação quando solicitado.

As fontes auxiliares classificam os bens por plaqueta; não substituem o CSV
patrimonial. O painel **Situação da base** mostra a origem ativa, total de bens,
setores, distribuição de status, data da última preparação, fontes importadas e
disponibilidade das fotos.

### 2. Conferir por setor

Escolha um setor existente em **Conferência por setor** e clique em **Iniciar**.
A interface mostra 50 itens por página, com navegação quando houver mais
resultados. Edite status, estado, observação ou foto pelo botão **Editar**.

Também é possível incluir um bem localizado sem plaqueta. Selecione um setor
existente, informe descrição e demais dados solicitados. O sistema cria um
identificador provisório; ele não inventa uma plaqueta patrimonial oficial.
Descrições de itens importados não podem ser alteradas. Registros manuais sem
plaqueta podem ter a descrição corrigida.

### 3. Buscar e gerar relatórios

Na **Busca geral de bens**, combine texto, setores, status, presença de
plaqueta, fotos e período de datas. Os filtros selecionados aparecem abaixo dos
campos; **Limpar filtros** remove os critérios.

Os resultados também são paginados, com 50 itens por página. A contagem indica
o total encontrado. A paginação limita o que é enviado e exibido em cada página;
o Excel ou PDF gerado inclui todos os resultados que correspondem aos filtros,
não apenas a página visível.

**Relatório de pendências** gera o Excel gerencial com resumos e abas de
pendências. Pela busca, é possível gerar Excel ou PDF dos resultados filtrados.
Os arquivos ficam em `dados_web\relatorios` e podem ser baixados pela interface.

## Fotos

As fotos são anexadas individualmente ao editar um bem. Selecione uma imagem
`.jpg`, `.jpeg`, `.png`, `.bmp`, `.gif` ou `.webp` e clique em
**Salvar foto e alterações**. Nessa ação, as mudanças de status, estado e
observação da janela de edição também são salvas. O aplicativo não importa fotos
em ZIP.

As tabelas exibem miniaturas reduzidas. Clique em uma para abrir a imagem
original em outra aba. Se o painel indicar referências pendentes ou a tabela
mostrar **Foto indisponível**, confira se o arquivo existe em
`dados_web\dados_trabalho\Fotos`.

## Controle de itens previstos para leilão

Essa lista é **separada do cadastro patrimonial**. Ela permite importar Excel
`.xlsx` ou CSV, ou adicionar um item manualmente. A descrição é obrigatória;
plaqueta, setor e data são opcionais. No cadastro manual, o setor pode ser
escolhido entre os setores existentes.

Na importação, os cabeçalhos reconhecidos para descrição incluem `Descrição`,
`Bem` e `Item`; também podem ser usados `Plaqueta`, `Setor` e `Data`. O CSV
detecta o separador e aceita codificações UTF-8 ou Latin-1. A primeira linha do
Excel deve conter os cabeçalhos. Cada importação é **acrescentada** à lista
existente, sem substituir nem remover os registros anteriores.

Os itens começam como **Pendente de identificação** e são salvos em
`dados_web\dados_trabalho\itens_leilao.json`. Para comparar um item com a base,
clique em **Buscar possíveis correspondências**. O sistema sugere candidatos
por similaridade de descrição e considera plaqueta e setor quando informados.
Uma sugestão é apenas uma pista: confira os dados e confirme o vínculo
manualmente. O vínculo não altera a plaqueta, o estado ou o status patrimonial,
e não realiza baixa. O resultado do leilão e a baixa patrimonial não são
registrados por esse controle.

## Status e estado de conservação

**Status** descreve a situação da conferência patrimonial:

| Status | Significado no fluxo |
|---|---|
| `PEND` | Conferência pendente |
| `OK` | Conferido |
| `NAO_EMPLAC` | Item que precisa de emplacamento |
| `SEM_PLAQ` | Item sem plaqueta |
| `DIVERG` | Divergência encontrada |
| `N_LOC` | Item não localizado |
| `ALIEN` | Bem classificado como alienado |
| `BAIXA` | Baixa patrimonial |

Para encontrar todos os itens sem número, use o filtro **Plaqueta → Sem
plaqueta**; um item sem plaqueta pode estar com status `DIVERG`, por exemplo.

**Estado** descreve a condição do bem: `EM USO`, `DESUSO`, `INSERVIVEL` ou
`NAO_APLICAVEL`. Ao editar, os status `N_LOC` e `BAIXA` definem o estado como
`NAO_APLICAVEL`; para os demais status, um estado incompatível volta para
`EM USO`.

## Backups e restauração

**Criar backup agora** salva um arquivo ZIP em `dados_web\backups`. O backup
inclui os arquivos de entrada, planilhas de trabalho, fotos, lista de leilão e
relatórios. Baixe o ZIP pela interface para guardá-lo também em outro local.

Para restaurar, escolha um backup web listado na interface e confirme a
substituição. Antes da restauração, o aplicativo cria outro backup do estado
atual. São aceitos somente backups criados por esta aplicação web; backups do
aplicativo desktop não são compatíveis. No modo MySQL, a restauração também
sincroniza a base a partir das planilhas restauradas.

## Configurações úteis

- `PATRIMONIO_STORAGE`: `excel` (padrão) ou `mysql`.
- `PATRIMONIO_MYSQL_HOST`, `PATRIMONIO_MYSQL_PORT`,
  `PATRIMONIO_MYSQL_DATABASE`, `PATRIMONIO_MYSQL_USER` e
  `PATRIMONIO_MYSQL_PASSWORD`: conexão MySQL.
- `PATRIMONIO_WEB_HOST`: endereço de escuta do Flask. O padrão aceita
  conexões pelas interfaces de rede locais; `127.0.0.1` limita o acesso ao
  próprio computador.
- `PATRIMONIO_WEB_DATA_DIR`: pasta usada para os dados operacionais.

Não coloque senhas em comandos que serão compartilhados ou salvos em arquivos
do projeto. Mantenha o MySQL e o Flask no computador servidor.

## Testes

Na pasta do projeto, execute:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Os testes usam uma pasta temporária para os dados. A pasta `dados_web` é
excluída do Git para reduzir o risco de versionar informações patrimoniais
reais.
