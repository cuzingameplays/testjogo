# Como publicar o DubShow no Render (passo a passo)

Este projeto já está preparado para o **Render Free**.

## 1) Criar uma conta
1. Entre em: https://render.com/
2. Clique em **Get Started**.
3. Faça login com GitHub.

## 2) Criar um repositório no GitHub
1. Entre em: https://github.com/
2. Clique em **New repository**.
3. Nome sugerido: `dubshow-online`
4. Deixe como **Public** ou **Private**.
5. Clique em **Create repository**.

## 3) Enviar os arquivos do projeto
Você deve enviar o conteúdo da pasta `dubshow_online` para o GitHub.

### Forma mais fácil
- Arraste os arquivos da pasta `dubshow_online` para dentro do repositório no GitHub.
- Confirme em **Commit changes**.

Arquivos mais importantes:
- `app.py`
- `media_service.py`
- `requirements.txt`
- `Dockerfile`
- `render.yaml`
- pasta `static/`

## 4) Criar o serviço no Render
1. No painel do Render, clique em **New +**.
2. Escolha **Blueprint**.
3. Conecte sua conta GitHub se ele pedir.
4. Escolha o repositório `dubshow-online`.
5. O Render vai ler automaticamente o arquivo `render.yaml`.
6. Clique em **Apply** ou **Create Blueprint**.

## 5) Esperar o deploy terminar
- O primeiro deploy pode levar alguns minutos.
- Quando terminar, o Render vai mostrar um link parecido com:

```text
https://dubshow-online.onrender.com
```

## 6) Testar
1. Abra o link do Render.
2. Clique em **Play Online** ou **Singleplayer**.
3. No modo online, crie a sala e copie o link para enviar aos amigos.

## Observações importantes
- O plano gratuito do Render pode "dormir" após um tempo sem uso.
- Quando alguém abrir o link novamente, ele pode levar alguns segundos para acordar.
- Como o Render Free tem recursos limitados, prefira clipes curtos e vídeos leves.
- Alguns links do YouTube podem falhar por bloqueios do próprio YouTube no servidor. Se acontecer, teste outro vídeo.

## Se der erro no deploy
No Render, abra a aba **Logs** e veja a mensagem de erro.
Os problemas mais comuns são:
- arquivos não enviados ao GitHub;
- pasta errada enviada;
- deploy iniciado antes do upload completo;
- vídeo do YouTube bloqueado pelo servidor.
