# examples/aws-cloudformation/application/Dockerfile - Package the health-checked sample service.


# Supply an approved base image digest from the isolated build environment
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}

# Run the application without root privileges or writable source
WORKDIR /app
COPY server.py /app/server.py
USER 65532:65532

# Match the task definition's port and health-check interpreter
EXPOSE 8080
CMD ["python", "/app/server.py"]
