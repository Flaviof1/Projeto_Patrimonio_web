# Gestão Patrimonial — aplicação web

Aplicação em português hospedada no computador principal. Pode ser usada nesse
computador, na mesma rede local ou remotamente por uma rede privada Tailscale.
O servidor web não oferece autenticação própria: qualquer dispositivo que
consiga alcançar o endereço pode consultar e alterar os dados, importar
arquivos e baixar relatórios e backups. Restrinja o acesso a dispositivos e
pessoas de confiança; não publique a porta do app na internet.

O [Manual do Sistema (HTML)](MANUAL_USUARIO.html) explica a operação completa
da aplicação para quem faz a conferência patrimonial.

## Pré-requisitos

- Windows e Python 3.10 ou superior

## Instalação e início

No PowerShell, a partir desta pasta:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

No computador servidor, abra <http://127.0.0.1:8765>. O terminal também mostra
os endereços IPv4 disponíveis, como `http://192.168.1.20:8765`; use esse
endereço nos outros dispositivos conectados à mesma rede Wi-Fi ou cabeada.
Se o Windows Firewall perguntar, permita o acesso somente em redes privadas.
Os dispositivos precisam estar na mesma rede, sem isolamento de clientes no
roteador. Para encerrar, volte ao terminal e pressione `Ctrl+C`. O servidor não
inicia sozinho o navegador.

## Acesso remoto por Tailscale

O Tailscale cria uma rede privada entre os dispositivos autorizados. Instale o
aplicativo oficial no PC que hospeda o sistema e em cada aparelho que o usará
(por exemplo, o iPhone), e entre na mesma rede/conta Tailscale. O painel
administrativo em `https://login.tailscale.com/admin/machines` deve mostrar
ambos os dispositivos como conectados.

1. No PC servidor, mantenha o serviço MySQL e o Flask em execução. O computador
   não pode estar desligado ou suspenso durante a auditoria remota.
2. No painel Tailscale, localize o PC servidor em **Máquinas** e copie seu
   endereço IPv4 Tailscale (normalmente começa com `100.`). Esse endereço é
   estável e normalmente continua o mesmo após reiniciar o PC. Use o endereço
   listado para o PC, não o endereço do iPhone.
3. No iPhone, confirme que o Tailscale está conectado. No Safari, acesse
   `http://IP-DO-PC:8765`, substituindo o exemplo pelo endereço do PC.
   Exemplo do endereço verificado nesta instalação em 22/10/2026:
   `http://100.85.183.31:8765`.
4. Se o endereço deixar de funcionar, verifique primeiro se o Flask continua
   aberto no terminal, se o PC aparece como conectado no Tailscale e se o
   iPhone também está conectado à mesma rede Tailscale.

O endereço pode mudar se o dispositivo for removido da rede Tailscale e
adicionado novamente, se o Tailscale for redefinido/reinstalado perdendo o
registro do dispositivo, se o disco for apagado ou se um administrador alterar
manualmente o endereço. Desligar e ligar normalmente não gera outro IP.

### Passos simples depois de ligar/reiniciar o PC

O IP Tailscale normalmente permanece igual, mas o Flask para quando o Windows
é desligado. Para voltar a usar o sistema:

1. Ligue o PC e espere o Windows carregar; confirme que o ícone do Tailscale
   indica conexão. Entre na mesma conta, se for solicitado.
2. O serviço MySQL está configurado para iniciar automaticamente. Se o app
   indicar falha de banco, abra **Serviços** pelo menu Iniciar e confirme que
   `MySQL80` está “Em execução”; se estiver parado, clique com o botão direito
   e escolha **Iniciar**.
3. Escolha um terminal: pode usar **Terminal → Novo Terminal** no VS Code ou
   abrir **Windows PowerShell** ou **Prompt de Comando (CMD)** pelo menu Iniciar.
   O VS Code não é obrigatório; ele é apenas uma maneira de abrir o terminal.
4. Escolha o bloco correspondente ao terminal aberto e execute uma linha de
   cada vez.

   **Windows PowerShell:**
   ```powershell
   Set-Location "C:\Users\Flávio\Documents\Projeto_Patrimonio_web"
   $env:PATRIMONIO_STORAGE = "mysql"
   .\.venv\Scripts\python.exe app.py
   ```

   **Prompt de Comando (CMD):**
   ```cmd
   cd /d "C:\Users\Flávio\Documents\Projeto_Patrimonio_web"
   set PATRIMONIO_STORAGE=mysql
   .\.venv\Scripts\python.exe app.py
   ```

   Se o terminal do VS Code já estiver aberto na pasta do projeto, não precisa
   executar o comando que muda de pasta. Confira se o terminal é PowerShell ou
   CMD e use somente o bloco correspondente.

5. Digite a senha MySQL do aplicativo quando solicitada. Ela não aparece na
   tela enquanto é digitada; isso é normal. Deixe a janela do terminal aberta
   (pode minimizá-la).
6. No iPhone, conecte o Tailscale e abra no Safari
   `http://100.85.183.31:8765` (endereço atual confirmado para este PC).

Para parar o Flask normalmente, volte à mesma janela do terminal e pressione
`Ctrl+C`. No próximo uso, inicie-o novamente com os passos acima. O passo a
passo completo está no [Manual do Sistema (HTML)](MANUAL_USUARIO.html).

Não configure encaminhamento da porta `8765` no roteador, não exponha o Flask
diretamente à internet e não compartilhe a conta ou a rede Tailscale com quem
não deve editar o patrimônio. O app não possui usuários, senha ou modo
somente leitura: um aparelho autorizado tem as mesmas permissões de edição e
importação que o computador servidor. A rede Tailscale protege o caminho entre
os dispositivos, mas não substitui controle de acesso dentro do app.

## Armazenamento MySQL

Se `PATRIMONIO_STORAGE` não for configurada, o programa usa Excel. Nesta
instalação, a migração para MySQL local já foi feita e o servidor é iniciado
com `PATRIMONIO_STORAGE=mysql`; o banco `patrimonio_web` contém a base ativa.
Não rode novamente os passos de criação/migração abaixo em uma instalação já
configurada. Eles servem para uma nova instalação ou recuperação planejada.
Para preparar a migração ao MySQL local, crie um banco e um usuário próprio no
MySQL Workbench ou no cliente MySQL:

```sql
CREATE DATABASE patrimonio_web
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'patrimonio_app'@'127.0.0.1' IDENTIFIED BY 'defina-uma-senha-forte';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX
  ON patrimonio_web.* TO 'patrimonio_app'@'127.0.0.1';
```

No CMD, configure a senha do usuário do aplicativo pelo utilitário incluído.
Ele pede as senhas root e do app sem exibi-las ou gravá-las no comando, atualiza
a conta `patrimonio_app` e testa o acesso ao banco:

```cmd
.\.venv\Scripts\python.exe configurar_usuario_mysql.py
```

Depois, com o Flask parado, rode a migração. A senha do app e a confirmação
`MIGRAR` são solicitadas no terminal. O script cria um backup web e valida a
quantidade e os dados copiados. Ele não substitui uma tabela MySQL que já
contenha outros dados: se ela já tiver a mesma quantidade, verifica o conteúdo
existente sem sobrescrever; se houver divergência, interrompe para inspeção.
As planilhas originais e as fotos são preservadas.

```cmd
.\.venv\Scripts\python.exe migrar_mysql.py
```

Se for necessário reconstruir as duas planilhas espelho a partir do MySQL, use
o script de recuperação. Ele cria uma cópia das pastas de dados atuais antes de
gravar as planilhas:

```cmd
.\.venv\Scripts\python.exe recuperar_planilhas_mysql.py
```

Essa recuperação restaura os dados e as referências de fotos presentes no
MySQL, mas não recupera arquivos CSV, auxiliares ou imagens que não estejam no
banco ou nas pastas copiadas.

O aplicativo mantém planilhas espelho para compatibilidade com relatórios,
backups e etapas de preparação. No MySQL, os campos mais usados ficam em
colunas consultáveis da tabela `patrimonio_itens`; `dados_completos` preserva
também os demais campos originais da planilha. O MySQL deve permanecer no PC
servidor; os outros dispositivos acessam somente o Flask pela rede local ou
Tailscale. Após a migração validada, selecione o MySQL e inicie o Flask no
mesmo CMD:

```cmd
set PATRIMONIO_STORAGE=mysql
.\.venv\Scripts\python.exe app.py
```

Na inicialização, a senha do app é solicitada sem ser exibida. Para voltar ao
armazenamento em Excel, abra um CMD novo ou execute
`set PATRIMONIO_STORAGE=excel`.

O endereço de escuta pode ser restringido com a variável de ambiente
`PATRIMONIO_WEB_HOST=127.0.0.1`. Por padrão, o servidor aceita conexões pelas
interfaces de rede locais. Não encaminhe a porta `8765` no roteador nem exponha
o serviço à internet. O servidor atende uma solicitação por vez; evite editar
os mesmos dados simultaneamente em mais de um dispositivo.

## Dados e arquivos

Todos os arquivos operacionais ficam em `dados_web`, dentro desta pasta. Essa
área é independente do projeto desktop:

- `dados_web\dados_entrada`: CSV patrimonial e arquivos auxiliares importados
- `dados_web\dados_trabalho`: planilhas de conferência, histórico, versões e fotos
- `dados_web\relatorios`: relatórios gerados
- `dados_web\backups`: backups ZIP próprios da versão web

A primeira execução começa sem cadastro carregado. A seleção explícita de um
arquivo no navegador é necessária para importar; nenhum dado do projeto desktop
é lido, copiado ou migrado automaticamente. A preparação também pode usar,
quando importadas, as planilhas `Bens_Alienados.xlsx` e `Bens_Baixados.xlsx`.
O Excel de baixados deve conter uma coluna `Plaqueta`; na preparação, suas
plaquetas são cruzadas com o cadastro e os bens correspondentes recebem status
`BAIXA`. A planilha é apenas uma fonte de classificação e não substitui o CSV
patrimonial.

As planilhas de entrada são validadas antes de serem instaladas. Se já existir
uma fonte do mesmo tipo, a interface pede confirmação antes de substituí-la.
Uma preparação ou restauração cria um backup web de segurança antes de
substituir a base. A restauração só aceita backups criados por esta versão web;
um backup Excel do desktop não é compatível.

## Fluxo básico de trabalho

1. Importe o CSV patrimonial exportado pelo SH3.
2. Importe, quando disponíveis, os Excels de bens alienados e de bens baixados.
   A planilha de baixados deve ter uma coluna `Plaqueta`.
3. Clique em **Preparar Dados (SH3)**. O sistema cruza as plaquetas das fontes
   com o cadastro, marca automaticamente os bens alienados como `ALIEN` e os
   baixados como `BAIXA`, e informa quantas plaquetas da lista de baixados
   foram encontradas e aplicadas. Se uma plaqueta estiver nas duas listas,
   prevalece `ALIEN`.
4. Selecione um setor e clique em **Iniciar** para revisar os bens. Edite o
   status, estado e observação conforme a conferência. Use **Incluir bem
   localizado sem plaqueta** para cadastrar achados que ainda não constam na
   base; o sistema gera identificadores provisórios e não inventa plaquetas
   oficiais.
5. Na Busca Geral, combine texto, setor, status, presença de plaqueta, foto e
   período. O filtro **Plaqueta → Sem plaqueta** facilita localizar esses
   registros. **Limpar filtros** remove os critérios e refaz a busca sem
   restrições. Gere Excel ou PDF para exportar os resultados filtrados.

### Controle independente dos itens previstos para leilão

Use a área **Itens previstos para leilão** para importar uma lista Excel (`.xlsx`)
ou CSV, ou incluir registros manualmente. A coluna de descrição é obrigatória;
plaqueta, setor e data são opcionais. São reconhecidos cabeçalhos como
`Descrição`, `Bem` ou `Item`, além de `Plaqueta`, `Setor` e `Data`. Cada
importação é acrescentada à lista já existente.

Os registros ficam separados da base patrimonial em
`dados_web\dados_trabalho\itens_leilao.json` e são incluídos nos backups web.
Itens entram como **Pendente de identificação**. Para procurar bens da base,
solicite **Buscar possíveis correspondências** no item. As sugestões são
calculadas por semelhança de descrição, plaqueta informada e setor, mas não
são uma confirmação. Confira os dados e confirme o vínculo manualmente. O vínculo
não altera `ESTADO`, `STATUS` ou a plaqueta do cadastro, e não realiza baixa.
Esse controle registra apenas a inclusão na lista e a identificação do bem;
resultado do leilão e baixa patrimonial continuam sendo etapas separadas.
6. Gere o relatório gerencial Excel para obter resumos e abas de pendências, ou
   crie um backup web pela tela.

As fotos podem ser anexadas individualmente pela edição do bem. O sistema não
importa fotos em ZIP. Para incluir ou substituir uma imagem, abra a edição do
bem, selecione o arquivo e use **Salvar foto e alterações**. As alterações de
status, estado e observação feitas nessa janela também são salvas junto com a
foto.

As imagens são identificadas pelo conteúdo real, não apenas pela extensão do
nome. Se uma referência como `.jpeg` contiver dados de imagem PNG, o nome da
referência é preservado para associação e a aplicação serve a imagem com o
tipo de conteúdo correto.

As fotos já associadas aparecem como miniaturas nas tabelas de conferência e
busca. Clique em uma miniatura para abrir a imagem em tamanho original em outra
aba; se a referência não tiver arquivo correspondente, a interface informa que
a foto está indisponível.

Na edição, os status `N_LOC` e `BAIXA` definem o estado como `NAO_APLICAVEL`;
quando o estado é incompatível com outro status, ele volta para `EM USO`.
Descrições de registros patrimoniais importados não são editáveis. Os registros
manuais sem plaqueta podem ter a descrição alterada; registros importados de
planilhas auxiliares mantêm a descrição de origem.

## Dados e significado dos status

O painel **Situação da base** mostra a origem ativa dos dados, quantidade de
bens e setores, distribuição por status, horário da última preparação,
fontes importadas e disponibilidade das fotos. No modo atual, a origem ativa
aparece como MySQL; as planilhas espelho continuam sendo mantidas.

Os status disponíveis são `PEND`, `OK`, `NAO_EMPLAC`, `SEM_PLAQ`, `DIVERG`,
`N_LOC`, `ALIEN` e `BAIXA`. Eles descrevem a conferência patrimonial; em
particular, um item localizado sem plaqueta pode estar marcado como
`DIVERG` com a observação de pendência de cadastro. Portanto, para encontrar
todos os itens sem número, prefira o filtro de presença de plaqueta, não apenas
o status. `ALIEN` indica bem alienado e `BAIXA` indica baixa patrimonial; quando
uma plaqueta aparece nas duas fontes auxiliares, `ALIEN` prevalece.

Os arquivos operacionais ficam em `dados_web`, separados do projeto desktop:

- `dados_web\dados_entrada`: CSV patrimonial e planilhas auxiliares importadas
- `dados_web\dados_trabalho`: planilhas de conferência, histórico, versões e fotos
- `dados_web\relatorios`: relatórios gerados
- `dados_web\backups`: backups ZIP da versão web

No modo MySQL, a tabela `patrimonio_web.patrimonio_itens` é a fonte ativa de
leitura e gravação, e as planilhas espelho são atualizadas para compatibilidade
e recuperação. O serviço MySQL e o Flask rodam no PC servidor; não instale nem
configure acesso MySQL no iPhone ou nos demais aparelhos clientes. O endereço
Tailscale do servidor é dinâmico; consulte o painel de máquinas antes de
compartilhar ou usar o endereço registrado como exemplo neste README.

## Backup e restauração

**Criar backup agora** salva um ZIP com as entradas, planilhas de trabalho e
relatórios dentro de `dados_web\backups`. O arquivo pode ser baixado pela
interface para armazenamento externo. Para restaurar, use **Restaurar** ao lado
de um backup e confirme a substituição dos dados web atuais. Antes de restaurar,
a aplicação guarda automaticamente um novo backup do estado atual. As imagens
de bens são armazenadas em `dados_web\dados_trabalho\Fotos` e incluídas no ZIP.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

O diretório `dados_web` está excluído do Git para reduzir o risco de versionar
informações patrimoniais reais.
