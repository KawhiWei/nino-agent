FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY mcp/dotnet/ ./mcp/dotnet/
WORKDIR /src/mcp/dotnet
RUN dotnet restore Nino.Data.Mcp.slnx \
    && dotnet publish src/Nino.Data.Mcp/Nino.Data.Mcp.csproj \
       --configuration Release --no-restore --output /app/publish

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime
WORKDIR /app
COPY --from=build /app/publish/ ./
ENV NINO_DATA_MCP_URLS=http://0.0.0.0:8091
EXPOSE 8091
USER $APP_UID
ENTRYPOINT ["dotnet", "Nino.Data.Mcp.dll"]
