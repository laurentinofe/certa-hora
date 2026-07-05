# Roteiro de apresentação — Certa Hora

## Duração sugerida: 10 minutos

### 1. Problema e objetivo — 1 minuto

Apresentar a dificuldade de controlar jornadas, registros incompletos, horas extras e justificativas quando os dados ficam dispersos. Explicar que o Certa Hora centraliza o processo em uma aplicação web com perfis de funcionário e gestor.

### 2. Arquitetura — 1 minuto

Mostrar que o navegador acessa um servidor Python, responsável pelas regras de negócio e pela comunicação com o banco SQLite. Destacar que o horário válido vem do servidor e que senhas não são armazenadas em texto.

### 3. Fluxo do gestor — 2 minutos

1. Entrar como gestor.
2. Mostrar configurações da empresa e da jornada.
3. Cadastrar um funcionário com matrícula, admissão e cargo.
4. Mostrar edição, redefinição de senha e ativação/desativação.

### 4. Fluxo do funcionário — 2 minutos

1. Entrar com a matrícula criada.
2. Registrar o ponto e mostrar a identificação automática da marcação.
3. Explicar a confirmação para registros com menos de 30 segundos.
4. Mostrar histórico mensal e solicitação de correção.

### 5. Hora extra — 2 minutos

1. Usar o modo acadêmico para gerar uma jornada com uma hora extra.
2. Entrar como funcionário e justificar a hora extra no histórico.
3. Voltar ao gestor e analisar o motivo.
4. Ressaltar que a análise não altera as horas efetivamente registradas.

### 6. Gestão e confiabilidade — 1 minuto

Mostrar filtros, exportação Excel, auditoria e backup. Explicar que todas as alterações relevantes deixam rastros e que o banco pode ser exportado e restaurado.

### 7. Encerramento — 1 minuto

Apresentar os testes automatizados e esclarecer que o sistema é acadêmico: aproxima-se de uma aplicação real, mas não é um REP-P homologado. Encerrar com possíveis evoluções, como implantação HTTPS, certificado digital e integração com folha de pagamento.

## Plano de contingência

- Manter um backup `.db` pronto.
- Executar `iniciar.bat` antes da apresentação.
- Manter abertas duas janelas anônimas: uma para gestor e outra para funcionário.
- Se a demonstração ao vivo falhar, usar o modo acadêmico e os registros previamente preparados.
