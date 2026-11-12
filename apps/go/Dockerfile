ARG GO_IMAGE
ARG TAGS=""
ARG CGO=0
FROM ${GO_IMAGE}
ARG TAGS
ARG CGO
RUN if [ -n "$TAGS" ]; then apt-get update -qq && apt-get install -y -qq --no-install-recommends libvips-dev pkg-config >/dev/null; fi
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=$CGO go build -tags "$TAGS" -trimpath -o /server ./cmd/server
CMD ["/server"]
