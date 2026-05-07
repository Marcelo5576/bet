# ApexGol AI - acesso permanente do Codex ao servidor

Este documento deixa o acesso ao servidor previsível, revogável e sem senha salva em código.

## Modelo recomendado

### Modo A - repositório Git

Fluxo recomendado:

1. Codex altera o repositório local.
2. Você envia para o GitHub.
3. No servidor, rode:

```bash
cd /opt/betsignal-cloud
./deploy_apexgol.sh
```

Vantagens:

- mais seguro
- auditável
- rollback simples
- não depende de ZIP manual

### Modo B - acesso remoto via SSH

Use quando quiser que o Codex ou outro operador faça deploy diretamente no servidor.

Regras:

- não usar senha root permanente
- usar chave SSH
- preferir usuário `deploy`
- dar sudo apenas para o mínimo necessário

## Como gerar chave SSH

No seu computador:

```bash
ssh-keygen -t ed25519 -C "codex-apexgol"
```

Isso cria:

- chave privada: `~/.ssh/id_ed25519`
- chave pública: `~/.ssh/id_ed25519.pub`

## Como instalar a chave no servidor

No servidor, como root:

```bash
PUBLIC_KEY_FILE=/root/id_ed25519.pub \
PROJECT_DIR=/opt/betsignal-cloud \
./create_deploy_user.sh
```

Ou cole a chave pública na hora quando o script pedir.

## Como usar o usuário deploy

Depois da criação:

```bash
ssh deploy@SEU_SERVIDOR
cd /opt/betsignal-cloud
./deploy_apexgol.sh
```

## Como configurar remoto no Codex

Se o ambiente permitir conexão SSH remota:

1. use o usuário `deploy`
2. aponte a chave privada local
3. conecte em `deploy@IP_DO_SERVIDOR`
4. trabalhe sempre no projeto:

```bash
cd /opt/betsignal-cloud
```

## Como revogar acesso

No servidor:

1. remova a chave em:

```bash
/home/deploy/.ssh/authorized_keys
```

2. se necessário, desative o usuário:

```bash
usermod -L deploy
```

3. para remover completamente:

```bash
userdel -r deploy
rm -f /etc/sudoers.d/apexgol-deploy
```

## Riscos e boas práticas

- nunca commitar `.env`
- nunca commitar tokens ou service role
- não usar root para deploy do dia a dia
- manter backups antes de cada deploy
- revisar `deploy_report.txt` e `diagnostics_report.txt`
- rotacionar chaves SSH quando necessário

## Comandos úteis

Deploy:

```bash
cd /opt/betsignal-cloud
./deploy_apexgol.sh
```

Diagnóstico:

```bash
cd /opt/betsignal-cloud
./diagnostics.sh
```

Rollback:

```bash
cd /opt/betsignal-cloud
./rollback_apexgol.sh
```
