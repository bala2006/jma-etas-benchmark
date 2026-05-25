FROM rocker/r-ver:4.3.3

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    gfortran \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt /workspace/requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

RUN Rscript -e "install.packages('ETAS', repos='https://cloud.r-project.org')"

COPY . /workspace
RUN chmod +x /workspace/run_pipeline.sh

CMD ["bash", "run_pipeline.sh"]
