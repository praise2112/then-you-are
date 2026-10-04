FROM ghcr.io/ggml-org/llama.cpp:server
COPY house.gguf /models/house.gguf
CMD ["-m", "/models/house.gguf", "-a", "student", "--host", "0.0.0.0", "--port", "8080", \
     "-t", "4", "-np", "2", "-c", "8192", "-cram", "256"]
