using System.Security.Cryptography;
using System.Text.Json;
using UnityEngine;

namespace BattleHost;

public sealed class BattleEnvironment(int width, int height, int maxUnits, int maxBases,
    int partySize, int routeCount, int maximumWaypoints)
{
    const int Wait = 0, CancelRouteEdit = 1, DeployStart = 2;
    const int GlobalFeatures = 15, PartyFeatures = 8, BaseFeatures = 8;
    static readonly int UnitFeatures = 19 + StatusEffectIdResolver.All.Count;
    const int MapFeatures = 2, RouteFeatures = 8, WaypointFeatures = 2;
    readonly string engineHash = Convert.ToHexString(SHA256.HashData(
        File.ReadAllBytes(typeof(BattleEnvironment).Assembly.Location)));
    readonly List<int> history = [];
    JsonElement scenario;
    int seed;
    BattleSimulation sim;
    int RouteStart => DeployStart + partySize;
    int RemoveStart => RouteStart + routeCount;
    int BaseStart => RemoveStart + routeCount;
    int CellStart => BaseStart + maxBases;
    public object Spec() => new { observation_size = GlobalFeatures + partySize * PartyFeatures +
        maxBases * BaseFeatures + maxUnits * UnitFeatures + width * height * MapFeatures +
        routeCount * (RouteFeatures + maximumWaypoints * WaypointFeatures) + maxUnits,
        action_names = ActionNames(),
        schema_id = $"battle-v3-party-routes-{width}-{height}-{maxUnits}-{maxBases}-{partySize}-{routeCount}-{maximumWaypoints}-{engineHash}" };
    string[] ActionNames()
    {
        var names = new List<string> { "wait", "cancel-route-edit" };
        names.AddRange(Enumerable.Range(0, partySize).Select(i => $"deploy:{i}"));
        names.AddRange(Enumerable.Range(0, routeCount).Select(i => $"route:{i}"));
        names.AddRange(Enumerable.Range(0, routeCount).Select(i => $"route-remove:{i}"));
        names.AddRange(Enumerable.Range(0, maxBases).Select(i => $"base:{i}"));
        names.AddRange(Enumerable.Range(0, width * height).Select(i => $"cell:{i % width}:{i / width}"));
        return names.ToArray();
    }
    public object Reset(JsonElement input, int inputSeed)
    {
        scenario = input.Clone(); seed = inputSeed; history.Clear();
        var data = Wire.Read<BattleData>(scenario.GetProperty("parameters").GetProperty("battle"));
        if (data.Stage.Width > width || data.Stage.Height > height || data.Stage.Bases.Length > maxBases ||
            data.Rules.PartySize != partySize || data.Rules.RouteCount != routeCount ||
            data.Rules.MaximumWaypoints != maximumWaypoints)
            throw new ArgumentException("Battle exceeds observation envelope or party/route schema");
        sim = new(data, seed, maxUnits);
        return Observe();
    }
    int GridCell(int action)
    {
        int index = action - CellStart, x = index % width, y = index / width;
        return x < sim.Grid.Width && y < sim.Grid.Height ? y * sim.Grid.Width + x : -1;
    }
    bool[] Mask()
    {
        var mask = new bool[CellStart + width * height]; mask[Wait] = true;
        if (sim.Finished) return mask;
        mask[CancelRouteEdit] = sim.Routes.RestartPending;
        for (int i = 0; i < partySize; i++) mask[DeployStart + i] = sim.CanDeploy(i);
        for (int i = 0; i < routeCount; i++)
        {
            mask[RouteStart + i] = sim.Bases.Any(b => b.Team == BattleSimulation.Ally && b.Alive);
            mask[RemoveStart + i] = sim.Routes.Routes[i] != null;
        }
        for (int i = 0; i < sim.Bases.Count; i++)
            mask[BaseStart + i] = sim.Routes.Current != null && sim.Bases[i].Team == BattleSimulation.Ally && sim.Bases[i].Alive;
        for (int i = 0; i < width * height; i++)
            mask[CellStart + i] = sim.Routes.CanEdit(GridCell(CellStart + i));
        return mask;
    }
    public object Step(int action)
    {
        if (sim == null || sim.Finished) throw new InvalidOperationException("Reset before step");
        var mask = Mask();
        if (action < 0 || action >= mask.Length || !mask[action]) throw new ArgumentException("Illegal action");
        sim.Events.Clear();
        if (action == CancelRouteEdit) sim.Routes.CancelRestart();
        else if (action >= DeployStart && action < RouteStart) sim.Deploy(action - DeployStart);
        else if (action >= RouteStart && action < RemoveStart) sim.Routes.Select(action - RouteStart);
        else if (action >= RemoveStart && action < BaseStart) sim.Routes.Remove(action - RemoveStart);
        else if (action >= BaseStart && action < CellStart) sim.Routes.SetOrigin(sim.Bases[action - BaseStart].Id);
        else if (action >= CellStart) sim.Routes.Edit(GridCell(action));
        // Real-time controls: every sampled decision advances the same amount of simulation time.
        sim.Advance();
        history.Add(action);
        bool terminated = sim.Outcome is "victory" or "defeat";
        var main = sim.Bases.First(b => b.Definition.Main);
        float reward = sim.Outcome == "victory" ? sim.Data.Rules.WinReward +
            sim.Data.Rules.HpRewardWeight * main.Hp / main.MaximumHp :
            sim.Outcome == "defeat" ? sim.Data.Rules.LossReward : 0;
        return new { observation = Observe(), reward, terminated, truncated = sim.Outcome == "timeout",
            info = new { outcome = sim.Outcome, scenario_id = scenario.GetProperty("id").GetString(),
                discount_steps = 1, simulation_seconds = sim.Time,
                metrics = sim.Metrics(), events = sim.Events.ToArray() } };
    }
    float CharacterIndex(string id) =>
        (Array.FindIndex(sim.Data.AllyDefinitions, d => d.Id == id) + 1f) /
        Math.Max(1, sim.Data.AllyDefinitions.Length);
    float X(int cell) => cell < 0 ? 0 : (float)(cell % sim.Grid.Width) / width;
    float Y(int cell) => cell < 0 ? 0 : (float)(cell / sim.Grid.Width) / height;
    static float DefenseValue(int defense) => (float)(defense / (CombatDamageCalculator.DefenseScale + defense));
    object Observe()
    {
        var values = new List<float>();
        var r = sim.Data.Rules;
        int unitCapacity = Math.Max(1, maxUnits);
        var route = sim.Routes.Current;
        var forecast = sim.Forecast;
        var forecastBase = forecast == null ? null : sim.Bases.First(b => b.Id == forecast.Origin);
        values.AddRange([sim.Time / r.EpisodeSeconds, (float)sim.Energy[0].Current / r.MaxEnergy,
            sim.Energy[0].Progress, (float)sim.Squads.Count(s => s.Team == BattleSimulation.Ally) / unitCapacity,
            sim.Routes.RestartPending ? 1 : 0, (float)sim.Party.Characters.Count / partySize,
            X(route?.Destination ?? -1), Y(route?.Destination ?? -1),
            (float)(sim.Routes.Selected + 1) / routeCount, (float)sim.Routes.Routes.Count(r => r != null) / routeCount,
            forecast == null ? 0 : 1, forecast == null ? 0 : forecast.Remaining / r.EpisodeSeconds,
            forecast == null ? 0 : (float)forecast.Characters.Count / unitCapacity,
            forecastBase == null ? 0 : (float)forecastBase.Definition.Cell[0] / width,
            forecastBase == null ? 0 : (float)forecastBase.Definition.Cell[1] / height]);
        for (int i = 0; i < partySize; i++)
        {
            if (i >= sim.Party.Characters.Count) { values.AddRange(new float[PartyFeatures]); continue; }
            var d = sim.Definition(sim.Party.Characters[i], BattleSimulation.Ally);
            values.AddRange([1, CharacterIndex(d.Id), (float)d.Cost / r.MaxEnergy,
                d.Hp / (float)sim.Data.AllyDefinitions.Max(d => d.Hp),
                d.Power / (float)sim.Data.AllyDefinitions.Max(d => d.Power),
                d.Speed / sim.Data.AllyDefinitions.Max(d => d.Speed), DefenseValue(d.Defense),
                sim.Units.Count(u => u.Team == BattleSimulation.Ally && u.Definition.Id == d.Id) / (float)unitCapacity]);
        }
        for (int i = 0; i < maxBases; i++)
        {
            if (i >= sim.Bases.Count) { values.AddRange(new float[BaseFeatures]); continue; }
            var b = sim.Bases[i];
            values.AddRange([1, b.Team, (float)Math.Max(0, b.Hp) / b.MaximumHp,
                b.Transform.position.x / (width * GameConfig.cellSize),
                b.Transform.position.z / (height * GameConfig.cellSize),
                b.Definition.Main ? 1 : 0, b.Definition.Boss ? 1 : 0, sim.Routes.Origin(route)?.Id == b.Id ? 1 : 0]);
        }
        var units = sim.Units.OrderBy(u => u.Id).ToArray();
        for (int i = 0; i < maxUnits; i++)
        {
            if (i >= units.Length) { values.AddRange(new float[UnitFeatures]); continue; }
            var u = units[i];
            values.AddRange([1, u.Team, CharacterIndex(u.Definition.Id), (float)Math.Max(0, u.Hp) / u.MaximumHp,
                u.Transform.position.x / (width * GameConfig.cellSize),
                u.Transform.position.z / (height * GameConfig.cellSize),
                u.Transform.forward.x, u.Transform.forward.z, u.Spawned ? 1 : 0,
                u.Attacking ? 1 : 0, u.Status.Active.Count / (float)StatusEffectIdResolver.All.Count,
                X(u.Destination), Y(u.Destination), u.Cooldown / r.EpisodeSeconds, u.Windup / r.EpisodeSeconds,
                u.Team == BattleSimulation.Ally && u.Route == sim.Routes.Selected && u.Route >= 0 ? 1 : 0,
                (float)u.Order / unitCapacity, (float)(u.Route + 1) / routeCount,
                (float)u.PathProgress / Math.Max(1, u.Path.Length - 1)]);
            foreach (var id in StatusEffectIdResolver.All)
                values.Add(u.Status.RemainingSeconds(id) / r.EpisodeSeconds);
        }
        for (int i = 0; i < width * height; i++)
        {
            int cell = GridCell(CellStart + i);
            values.Add(cell >= 0 && sim.Grid.Walkable(cell) ? 1 : 0);
            values.Add(cell >= 0 && sim.Grid.BaseCells.Contains(cell) ? 1 : 0);
        }
        foreach (var entry in sim.Routes.Routes)
        {
            if (entry == null) { values.AddRange(new float[RouteFeatures + maximumWaypoints * WaypointFeatures]); continue; }
            values.AddRange([1, entry.Slot == sim.Routes.Selected ? 1 : 0,
                (float)((sim.Routes.Origin(entry)?.Id ?? -1) + 1) / (maxBases + 1), X(entry.Destination), Y(entry.Destination),
                (float)entry.Waypoints.Count / Math.Max(1, maximumWaypoints),
                sim.Units.Count(u => u.Team == BattleSimulation.Ally && u.Route == entry.Slot) / (float)unitCapacity,
                sim.Routes.Path(entry).Length > 0 ? 1 : 0]);
            for (int i = 0; i < maximumWaypoints; i++)
                values.AddRange(i < entry.Waypoints.Count ? [X(entry.Waypoints[i]), Y(entry.Waypoints[i])] : new float[WaypointFeatures]);
        }
        for (int i = 0; i < maxUnits; i++) values.Add(i < units.Length ? DefenseValue(units[i].Defense) : 0);
        return new { values, action_mask = Mask() };
    }
    public object Save() => new { engine_hash = engineHash, scenario, seed, actions = history.ToArray(),
        debug = new { tick = sim.Tick, outcome = sim.Outcome, energy = sim.Energy.Select(e => e.Current).ToArray(),
            party = sim.Party.Characters.ToArray(), selected_route = sim.Routes.Selected, restart_pending = sim.Routes.RestartPending,
            routes = sim.Routes.Routes.Select(r => r == null ? null : new { r.Slot, r.Origin, r.Destination, waypoints = r.Waypoints.ToArray() }).ToArray(),
            units = sim.Units.Select(u => new { u.Id, u.Team, character = u.Definition.Id, u.Hp, u.Defense,
                x = u.Transform.position.x, z = u.Transform.position.z, u.Spawned, u.Squad, u.Order,
                u.Destination, u.Route, path = u.Path, u.PathProgress, u.Attacking, u.Cooldown, u.Windup,
                statuses = u.Status.Active.Select(id => new { id = (int)id, seconds = u.Status.RemainingSeconds(id) }).ToArray() }).ToArray(),
            bases = sim.Bases.Select(b => new { b.Id, b.Team, b.Hp }).ToArray(), metrics = sim.Metrics() } };
    public object Load(JsonElement state)
    {
        if (state.GetProperty("engine_hash").GetString() != engineHash) throw new ArgumentException("Engine changed");
        Reset(state.GetProperty("scenario"), state.GetProperty("seed").GetInt32());
        foreach (var action in state.GetProperty("actions").EnumerateArray()) Step(action.GetInt32());
        return Observe();
    }
}
