using Microsoft.Extensions.Logging.Console;
using Nino.Data.Mcp.Data;
using Npgsql;

const string defaultConnectionString =
    "Host=localhost;Port=55432;Database=nino_data_demo;Username=nino_data_readonly;Password=nino_data_readonly_password";

var useStdio = args.Any(arg => string.Equals(arg, "--stdio", StringComparison.OrdinalIgnoreCase));
var connectionString = Environment.GetEnvironmentVariable("NINO_DATA_DB_CONNECTION_STRING")
                       ?? defaultConnectionString;

static void AddDataServices(IServiceCollection services, string connectionString)
{
    var connectionBuilder = new NpgsqlConnectionStringBuilder(connectionString)
    {
        ApplicationName = "Nino.Data.Mcp",
        CommandTimeout = 15,
        Timeout = 5,
        ReadBufferSize = 8192
    };
    services.AddSingleton(NpgsqlDataSource.Create(connectionBuilder.ConnectionString));
    services.AddScoped<IDataQueryService, DataQueryService>();
}

if (useStdio)
{
    var builder = Host.CreateApplicationBuilder(args);
    builder.Logging.ClearProviders();
    builder.Logging.AddConsole(options =>
        options.LogToStandardErrorThreshold = LogLevel.Trace);
    AddDataServices(builder.Services, connectionString);
    builder.Services
        .AddMcpServer()
        .WithStdioServerTransport()
        .WithToolsFromAssembly();
    await builder.Build().RunAsync();
    return;
}

var webBuilder = WebApplication.CreateBuilder(args);
webBuilder.WebHost.UseUrls(Environment.GetEnvironmentVariable("NINO_DATA_MCP_URLS")
                           ?? "http://127.0.0.1:8091");
AddDataServices(webBuilder.Services, connectionString);
webBuilder.Services
    .AddMcpServer()
    .WithHttpTransport(options => options.Stateless = true)
    .WithToolsFromAssembly();

var app = webBuilder.Build();
app.MapGet("/health", async (NpgsqlDataSource dataSource, CancellationToken cancellationToken) =>
{
    try
    {
        await using var command = dataSource.CreateCommand("SELECT 1");
        await command.ExecuteScalarAsync(cancellationToken);
        return Results.Ok(new
        {
            status = "ok",
            service = "nino-data",
            version = "0.1.0",
            transport = "streamable-http"
        });
    }
    catch
    {
        return Results.Json(new { status = "unhealthy" }, statusCode: 503);
    }
});
app.MapMcp("/mcp");
await app.RunAsync();
