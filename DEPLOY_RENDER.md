# Implantação temporária no Render

O projeto está preparado para funcionar como **Web Service** no Render.

## Configuração automática

O arquivo `render.yaml` define:

- Runtime: Python
- Plano: Free
- Build: `pip install -r requirements.txt`
- Start: `python server.py`
- Host: `0.0.0.0`
- Health check: `/api/me`

## Observação sobre os dados

O plano gratuito possui armazenamento efêmero. O banco `ponto.db` é criado automaticamente com os usuários demonstrativos, mas pode ser recriado quando o serviço hibernar, reiniciar ou receber um novo deploy.

Credenciais iniciais:

- Gestor: `admin` / `Admin@123`
- Funcionário: `1001` / `Teste@123`

Use o modo acadêmico para montar rapidamente os cenários da apresentação.
