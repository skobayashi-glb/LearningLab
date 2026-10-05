using System.Text.Json;
using BattleHost;

const int DefaultWidth = 24, DefaultHeight = 24, DefaultUnits = 64,
    DefaultBases = 8, DefaultHand = 8, DefaultSquads = 5;
int[] sizes = args.Length == 0 ? [DefaultWidth, DefaultHeight, DefaultUnits, DefaultBases, DefaultHand, DefaultSquads]
    : args.Select(int.Parse).ToArray();
if (sizes.Length != 6 || sizes.Any(s => s <= 0)) throw new ArgumentException("Expected width height units bases hand squads");
var environment = new BattleEnvironment(sizes[0], sizes[1], sizes[2], sizes[3], sizes[4], sizes[5]);
string line;
while ((line = Console.ReadLine()) != null)
{
    int id = 0;
    object response;
    try
    {
        using var document = JsonDocument.Parse(line);
        var request = document.RootElement;
        id = request.GetProperty("id").GetInt32();
        if (request.GetProperty("version").GetInt32() != Wire.Version) throw new ArgumentException("Protocol version mismatch");
        var payload = request.GetProperty("payload");
        object result = request.GetProperty("operation").GetString() switch
        {
            "spec" => environment.Spec(),
            "reset" => environment.Reset(payload.GetProperty("scenario"), payload.GetProperty("seed").GetInt32()),
            "step" => environment.Step(payload.GetProperty("action").GetInt32()),
            "save_state" => environment.Save(),
            "load_state" => environment.Load(payload.GetProperty("state")),
            _ => throw new ArgumentException("Unknown operation")
        };
        response = new { version = Wire.Version, id, result };
    }
    catch (Exception error)
    {
        Console.Error.WriteLine(error);
        response = new { version = Wire.Version, id, error = error.Message };
    }
    Console.WriteLine(JsonSerializer.Serialize(response, Wire.Options));
}
