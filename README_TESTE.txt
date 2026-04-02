PROJETO PRONTO PARA TESTE

1) Instale o Flask:
   pip install flask

2) Rode o projeto:
   python app.py

3) Abra no navegador:
   http://127.0.0.1:5000/

4) Link público de agendamento:
   http://127.0.0.1:5000/agendar/barbearia

5) Link interno da agenda do dia (com token):
   http://127.0.0.1:5000/agenda/barbearia?token=troque-este-token-em-producao

6) Exportação CSV:
   http://127.0.0.1:5000/exportar-csv?empresa=barbearia&token=troque-este-token-em-producao

7) Para zerar novamente o banco de teste:
   python reset_db.py

OBS:
- O arquivo adm.html foi mantido, mas o fluxo principal não depende dele.
- O banco já vai zerado para testes de agendamento.
