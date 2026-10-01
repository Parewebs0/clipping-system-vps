FROM python:3.12-slim
WORKDIR /opt/clipping-system
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# gog CLI (Google Drive listing for paso 3b drive_resolver_tick). Config/keyring are bind-mounted.
COPY gog/bin/gog /usr/local/bin/gog
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
