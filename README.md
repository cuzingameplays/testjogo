# DubShow Online Render Edition

Esta é a edição focada no **Render** do DubShow Online.

## O que esta versão traz
- visual inspirado no layout neon/cyan da referência enviada;
- fluxo com **Play Online** e **Singleplayer**;
- suporte a salas de **1 a 5 jogadores**;
- seleção de mídia por **link do YouTube**;
- gravação pelo navegador;
- ranking por similaridade de áudio;
- geração do vídeo dublado;
- projeto pronto para publicar no **Render Free**.

## Estrutura principal
- `app.py` → servidor FastAPI
- `media_service.py` → download do YouTube, corte, separação leve e render do resultado
- `scoring.py` → análise e pontuação do áudio
- `room_manager.py` → salas e WebSocket
- `static/` → interface visual
- `Dockerfile` e `render.yaml` → deploy no Render

## Melhorias desta edição
- Designer ajustado para ficar mais próximo do visual mostrado na referência do usuário.
- Mixagem final revista para a voz do jogador não ficar tão "por cima" da trilha.
- Sincronização melhorada com alinhamento automático entre a referência e a gravação.
- Redução mais forte da voz central original, preservando melhor a trilha de fundo.

## Publicação no Render
Leia o arquivo:

`COMO_PUBLICAR_NO_RENDER_PASSO_A_PASSO.md`

Ele explica exatamente como:
1. criar o repositório no GitHub;
2. enviar os arquivos;
3. conectar no Render;
4. publicar o jogo;
5. obter o link online.

## Rodar localmente
```bash
python -m pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8765
```

Depois abra:

```text
http://127.0.0.1:8765
```

## Observação técnica
A separação da fala usa um método leve baseado no canal central. Isso foi escolhido para continuar compatível com hospedagem gratuita. Em muitos vídeos o resultado fica bom, mas não é uma separação perfeita como ferramentas pesadas de IA.
