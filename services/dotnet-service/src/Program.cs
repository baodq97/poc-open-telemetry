using Azure.Monitor.OpenTelemetry.AspNetCore;
using Microsoft.AspNetCore.Mvc;
using System.Linq;
using OpenTelemetry.Exporter;
using OpenTelemetry.Trace;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

// Configure Azure Monitor OpenTelemetry using APPLICATIONINSIGHTS_CONNECTION_STRING
var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") ?? "http://apm-server:4318/v1/traces";
builder.Services
		.AddOpenTelemetry()
		.UseAzureMonitor()
		.WithTracing(t =>
		{
			t.AddOtlpExporter(o =>
			{
				o.Endpoint = new Uri(otlpEndpoint);
				o.Protocol = OtlpExportProtocol.HttpProtobuf;
			});
		});

var app = builder.Build();

app.UseSwagger();
app.UseSwaggerUI();

app.MapGet("/healthz", () => Results.Ok(new { status = "ok" }));

app.MapPost("/analyze", ([FromBody] AnalyzeRequest req) =>
{
	var text = req.Text ?? string.Empty;
	var length = text.Length;
	var uppercaseCount = text.Count(char.IsUpper);
	return Results.Ok(new { length, uppercaseCount });
});

app.Run();

public record AnalyzeRequest(string? Text);


