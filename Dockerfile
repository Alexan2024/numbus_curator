FROM python:3.11-slim

# libfribidi0 — нужна Pillow для полноценной вёрстки текста (Raqm):
# кернинг и лигатуры как в браузере, иначе превью и итог разойдутся.
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core curl ca-certificates libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Шрифты каталога (лицензия OFL) скачиваются при сборке с зафиксированного
# коммита Google Fonts — сборка не сломается, если у них что-то переименуют.
ARG GF=https://raw.githubusercontent.com/google/fonts/23e54b51ddffbc7713c583748e3bd86f62b1fa4a/ofl
RUN mkdir -p fonts && cd fonts \
    && curl -fsSL "$GF/inter/Inter%5Bopsz,wght%5D.ttf"                           -o Inter.ttf \
    && curl -fsSL "$GF/onest/Onest%5Bwght%5D.ttf"                                -o Onest.ttf \
    && curl -fsSL "$GF/golostext/GolosText%5Bwght%5D.ttf"                        -o GolosText.ttf \
    && curl -fsSL "$GF/manrope/Manrope%5Bwght%5D.ttf"                            -o Manrope.ttf \
    && curl -fsSL "$GF/geologica/Geologica%5BCRSV,SHRP,slnt,wght%5D.ttf"         -o Geologica.ttf \
    && curl -fsSL "$GF/montserrat/Montserrat%5Bwght%5D.ttf"                      -o Montserrat.ttf \
    && curl -fsSL "$GF/jost/Jost%5Bwght%5D.ttf"                                  -o Jost.ttf \
    && curl -fsSL "$GF/rubik/Rubik%5Bwght%5D.ttf"                                -o Rubik.ttf \
    && curl -fsSL "$GF/nunito/Nunito%5Bwght%5D.ttf"                              -o Nunito.ttf \
    && curl -fsSL "$GF/comfortaa/Comfortaa%5Bwght%5D.ttf"                        -o Comfortaa.ttf \
    && curl -fsSL "$GF/unbounded/Unbounded%5Bwght%5D.ttf"                        -o Unbounded.ttf \
    && curl -fsSL "$GF/oswald/Oswald%5Bwght%5D.ttf"                              -o Oswald.ttf \
    && curl -fsSL "$GF/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf"            -o PlayfairDisplay.ttf \
    && curl -fsSL "$GF/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf"        -o CormorantGaramond.ttf \
    && curl -fsSL "$GF/lora/Lora%5Bwght%5D.ttf"                                  -o Lora.ttf \
    && curl -fsSL "$GF/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"                -o JetBrainsMono.ttf \
    && python -c "from PIL import features; print('Raqm (кернинг как в браузере):', features.check('raqm'))"

COPY *.py webapp.html ./

CMD ["python", "bot.py"]
