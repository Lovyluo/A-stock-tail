// In-memory test handler only; no sockets or production HTTP handler.
using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Diagnostics;
using System.Globalization;
public class T29Handler : HttpMessageHandler {
    public string Marker;
    public string Body = "{\"test_only\":true}";
    public int BlockMs;
    public int BodyMs;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken t) {
        File.WriteAllText(Marker, ((double)Stopwatch.GetTimestamp()/Stopwatch.Frequency).ToString("R", CultureInfo.InvariantCulture));
        if (BlockMs > 0) Thread.Sleep(BlockMs);
        var response = new HttpResponseMessage(HttpStatusCode.OK);
        response.RequestMessage = r;
        response.Content = new StreamContent(new T29Body(BodyMs, Body));
        return Task.FromResult(response);
    }
}
public class T29Body : MemoryStream {
    private int DelayMs;
    public T29Body(int delay, string body) : base(System.Text.Encoding.UTF8.GetBytes(body)) { DelayMs=delay; }
    public override async Task<int> ReadAsync(byte[] b, int o, int n, CancellationToken t) {
        if (DelayMs > 0) { int d=DelayMs; DelayMs=0; await Task.Delay(d,t); }
        return await base.ReadAsync(b,o,n,t);
    }
}
