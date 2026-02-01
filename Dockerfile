FROM python:3.8

# Imposta la working directory
WORKDIR /

# Copia tutti i file della cartella del Dockerfile nella root del container
COPY . /

# Installa dipendenze se hai requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Espone la porta richiesta (8080 per la piattaforma cloud)
EXPOSE 8080

# Avvia il server
CMD ["python", "server.py"]
