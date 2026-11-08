FROM python:3.12-slim

# Бот не использует сторонних библиотек — только стандартная библиотека Python.
WORKDIR /app
COPY bot.py content.py ./

ENV PYTHONUNBUFFERED=1
# Токен и chat_id передаются переменными окружения платформы, не в образе.
CMD ["python3", "bot.py"]
