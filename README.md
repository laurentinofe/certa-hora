# Certa Hora — sistema acadêmico de ponto

Aplicação web acadêmica inspirada em um sistema real de controle de jornada. Usa somente a biblioteca padrão do Python e banco SQLite.

## Como executar

1. Dê dois cliques no arquivo `iniciar.bat`.
2. Mantenha a janela do servidor aberta.
3. Abra `http://127.0.0.1:8000` no navegador.

Alternativamente, em um computador com Python 3 configurado, execute `python server.py`.

Credenciais iniciais:

- Funcionário: matrícula `1001`, senha `Teste@123`
- Gestor: matrícula `admin`, senha `Admin@123`

O arquivo `ponto.db` é criado automaticamente na primeira execução.

## Implantação no Render

O repositório inclui `render.yaml`, `requirements.txt` e `.gitignore`. Consulte `DEPLOY_RENDER.md`.

No plano gratuito, o SQLite é temporário e pode ser recriado após hibernação, reinicialização ou novo deploy. Essa configuração destina-se somente à validação acadêmica.

## Funcionalidades

- Login separado por perfil, sessão HTTP e senhas protegidas com PBKDF2.
- Cadastro de novos funcionários realizado exclusivamente pelo gestor.
- Edição de dados, redefinição de senha e ativação ou desativação de funcionários.
- Modo acadêmico para simular jornadas regulares, incompletas e com hora extra.
  O modo acadêmico aceita datas passadas anteriores ao cadastro, sem gerar ausências retroativas.
- Análise administrativa da justificativa de hora extra sem alterar as horas apuradas.
- Histórico mensal do funcionário com justificativa retroativa de horas extras.
- Alteração de senha pelo funcionário e invalidação automática de sessões antigas.
- Configuração de empresa, jornada e tolerância pelo gestor.
- Auditoria filtrável e backup/restauração do banco SQLite.

## Testes automatizados

Dê dois cliques em `executar_testes.bat` ou execute:

`python -m unittest discover -s tests -p "test_*.py" -v`
- Marcação automática de entrada, almoço, retorno e saída.
- Confirmação para marcações realizadas em menos de 30 segundos.
- Apuração diária da jornada de 8h30, com tolerância diária de 10 minutos.
- Justificativa de hora extra.
- Solicitação e aprovação de correções sem apagar o registro original.
- Registro de feriados, folgas, atestados e outras ocorrências pelo gestor.
- Filtros por funcionário e período.
- Exportação em Excel `.xlsx`.
- Trilha de auditoria no banco.

## Observação

Este é um projeto acadêmico. Ele demonstra conceitos de segurança, integridade e rastreabilidade, mas não é um REP-P homologado e não implementa todos os requisitos fiscais e criptográficos da Portaria MTP nº 671/2021.
