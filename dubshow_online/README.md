# DubShow Online 2.0

Versão atualizada do jogo de dublagem online.

## Principais mudanças

- **Menu inicial novo** com visual inspirado no layout neon/cyan da referência enviada.
- **Modo Online** e **Modo Singleplayer**.
- **Somente YouTube** na seleção de mídia.
- **Melhor sincronização do resultado final**: a voz gravada é alinhada automaticamente antes da mixagem.
- **Mixagem revista**: a voz do jogador entra menos "em cima" e a trilha de fundo fica mais natural.
- **Atenuação mais forte da voz original central** para tentar preservar música e efeitos.

## Observação importante sobre o túnel da Cloudflare

Pelo seu arquivo `DIAGNOSTICO_TUNEL.txt`, DNS e porta 7844 estão funcionando. Isso indica que o problema não é um bloqueio simples de rede, e sim instabilidade do **Quick Tunnel** (`trycloudflare.com`) nessa sessão específica.

Por isso, para uso real, o mais recomendado é publicar no **Render Free**, que entrega um endereço fixo `onrender.com` e evita o erro de publicação temporária do túnel.

## Executar localmente

```bash
python -m pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8765
```

Depois, abra:

```text
http://127.0.0.1:8765
```

## Publicar grátis no Render

1. Suba a pasta para um repositório GitHub.
2. No Render, crie um **Web Service** ou use o `render.yaml`.
3. Publique usando Docker.
4. Compartilhe o link `https://seu-app.onrender.com`.

## Limitação técnica

A separação de diálogo **não é IA pesada**. Para continuar gratuita e leve, a aplicação usa uma técnica de **redução do canal central**. Isso melhora bastante em muitos vídeos, mas não é perfeito. Em cenas onde música, efeitos e voz estão todos no centro, ainda pode sobrar um pouco da fala original ou perder parte de alguns efeitos.
