# Vincular Telegram por Usuario

Este guia conecta o Telegram de cada cliente ao SaaS para receber notificacoes.

## 1) Abrir o bot

No Telegram, procure seu bot BetSignal e envie:

`/start`

Depois rode:

`/chatid`

O bot vai responder com um numero (chat_id).

## 2) Salvar no portal

1. Entre em `/login`.
2. Abra a pagina `/app`.
3. Em **Preferencias de scanner/notificacao**:
   - cole o `chat_id`;
   - marque **Quero notificacoes no Telegram**;
   - ajuste os tempos do scanner (sem jogo ativo / com jogo ativo);
   - clique em **Salvar preferencias**.

## 3) Como funciona

- Sem jogo ativo: ciclo padrao de 1 em 1 minuto.
- Com jogo escolhido: ciclo de 5 em 5 minutos.
- Cada usuario pode ajustar seus proprios tempos dentro dos limites permitidos.

## 4) Diagnostico rapido

Se nao chegar mensagem:

1. no Telegram, envie `/suporte`;
2. no painel, confira se o `chat_id` foi salvo sem espacos;
3. valide se a opcao de notificacao esta ativada;
4. veja se o bot esta online no servidor.
