using System.Security.Cryptography;
using System.Text.Json;
using UnityEngine;

namespace BattleHost;

public sealed class BattleEnvironment(int width, int height, int maxUnits, int maxBases, int handSize, int squadLimit)
{
    const int Wait = 0, Clear = 1, Commit = 2, Discard = 3, CardStart = 4;
    const int GlobalFeatures = 15, CardFeatures = 7, BaseFeatures = 8;
    static readonly int UnitFeatures = 17 + StatusEffectIdResolver.All.Count;
    const int MapFeatures = 2, SquadFeatures = 5;
    const int DraftSlack = 4;
    readonly string engineHash = Convert.ToHexString(SHA256.HashData(
        File.ReadAllBytes(typeof(BattleEnvironment).Assembly.Location)));
    readonly List<int> selected = [];
    readonly List<int> history = [];
    JsonElement scenario;
    int seed, origin = -1, destination = -1, selectedSquad = -1, draftDepth;
    BattleSimulation sim;
    int BaseStart => CardStart + handSize;
    int SquadStart => BaseStart + maxBases;
    int CellStart => SquadStart + squadLimit;
    int MaxDraft => handSize * 2 + DraftSlack;
    public object Spec() => new { observation_size = GlobalFeatures + handSize * CardFeatures +
        maxBases * BaseFeatures + maxUnits * UnitFeatures + width * height * MapFeatures +
        squadLimit * SquadFeatures + handSize + maxUnits,
        action_names = ActionNames(),
        schema_id = $"battle-v2-defense-{width}-{height}-{maxUnits}-{maxBases}-{handSize}-{squadLimit}-{engineHash}" };
    string[] ActionNames()
    {
        var names = new List<string> { "wait", "clear", "commit", "discard" };
        names.AddRange(Enumerable.Range(0, handSize).Select(i => $"card:{i}"));
        names.AddRange(Enumerable.Range(0, maxBases).Select(i => $"base:{i}"));
        names.AddRange(Enumerable.Range(0, squadLimit).Select(i => $"squad:{i}"));
        names.AddRange(Enumerable.Range(0, width * height).Select(i => $"cell:{i % width}:{i / width}"));
        return names.ToArray();
    }
    public object Reset(JsonElement input, int inputSeed)
    {
        scenario = input.Clone(); seed = inputSeed; history.Clear();
        var data = Wire.Read<BattleData>(scenario.GetProperty("parameters").GetProperty("battle"));
        if (data.Stage.Width > width || data.Stage.Height > height || data.Stage.Bases.Length > maxBases ||
            data.Rules.HandSize != handSize || data.Rules.SquadLimit != squadLimit)
            throw new ArgumentException("Battle exceeds observation envelope or hand/squad schema");
        sim = new(data, seed, maxUnits);
        ClearDraft();
        return Observe();
    }
    void ClearDraft()
    {
        selected.Clear(); selectedSquad = -1; draftDepth = 0;
        origin = sim.Bases.FirstOrDefault(b => b.Team == BattleSimulation.Ally && b.Alive)?.Id ?? -1;
        destination = sim.Grid.Index(sim.Bases.First(b => b.Definition.Boss).Definition.Cell);
    }
    int GridCell(int action)
    {
        int index = action - CellStart, x = index % width, y = index / width;
        return x < sim.Grid.Width && y < sim.Grid.Height ? y * sim.Grid.Width + x : -1;
    }
    Squad[] AllySquads() => sim.Squads.Where(s => s.Team == BattleSimulation.Ally).OrderBy(s => s.Id).ToArray();
    bool[] Mask()
    {
        var mask = new bool[CellStart + width * height]; mask[Wait] = true;
        if (sim.Finished) return mask;
        bool retarget = selectedSquad >= 0;
        mask[Commit] = retarget ? sim.CanRetarget(selectedSquad, destination) :
            sim.CanDeploy(selected, origin, destination);
        mask[Discard] = selected.Count > 0 && selected.Count * sim.Data.Rules.DiscardCost <= sim.Energy[0].Current;
        if (draftDepth >= MaxDraft) return mask;
        mask[Clear] = selected.Count > 0 || retarget;
        for (int i = 0; i < sim.Deck.Hand.Count; i++)
            mask[CardStart + i] = !retarget;
        for (int i = 0; i < sim.Bases.Count; i++)
            mask[BaseStart + i] = !retarget && sim.Bases[i].Team == BattleSimulation.Ally;
        var squads = AllySquads();
        for (int i = 0; i < squads.Length; i++) mask[SquadStart + i] = true;
        for (int i = 0; i < width * height; i++)
        {
            int cell = GridCell(CellStart + i);
            if (cell < 0) continue;
            if (retarget) mask[CellStart + i] = sim.CanRetarget(selectedSquad, cell);
            else
            {
                var b = sim.Bases.FirstOrDefault(b => b.Id == origin && b.Team == BattleSimulation.Ally);
                mask[CellStart + i] = b != null && b.SpawnIndices.Any(c => sim.Grid.Reachable(c, cell));
            }
        }
        return mask;
    }
    public object Step(int action)
    {
        if (sim == null || sim.Finished) throw new InvalidOperationException("Reset before step");
        var mask = Mask();
        if (action < 0 || action >= mask.Length || !mask[action]) throw new ArgumentException("Illegal action");
        bool advances = action is Wait or Commit or Discard;
        sim.Events.Clear();
        if (action == Commit)
        {
            if (selectedSquad >= 0) sim.Retarget(selectedSquad, destination);
            else sim.Deploy(selected, origin, destination);
        }
        else if (action == Discard) sim.Discard(selected);
        else if (action == Clear) { selected.Clear(); selectedSquad = -1; }
        else if (action >= CardStart && action < BaseStart)
        {
            int card = sim.Deck.Hand[action - CardStart];
            if (!selected.Remove(card)) selected.Add(card);
        }
        else if (action >= BaseStart && action < SquadStart) origin = sim.Bases[action - BaseStart].Id;
        else if (action >= SquadStart && action < CellStart)
        {
            selected.Clear(); var squad = AllySquads()[action - SquadStart];
            selectedSquad = squad.Id; destination = squad.Destination;
        }
        else if (action >= CellStart) destination = GridCell(action);
        if (advances) { sim.Advance(); ClearDraft(); } else draftDepth++;
        history.Add(action);
        bool terminated = sim.Outcome is "victory" or "defeat";
        float reward = sim.Outcome == "victory" ? sim.Data.Rules.WinReward +
            sim.Data.Rules.HpRewardWeight * sim.Bases.First(b => b.Definition.Main).Hp /
                sim.Bases.First(b => b.Definition.Main).MaximumHp :
            sim.Outcome == "defeat" ? sim.Data.Rules.LossReward : 0;
        return new { observation = Observe(), reward, terminated, truncated = sim.Outcome == "timeout",
            info = new { outcome = sim.Outcome, scenario_id = scenario.GetProperty("id").GetString(),
                discount_steps = advances ? 1 : 0, simulation_seconds = sim.Time,
                metrics = sim.Metrics(), events = sim.Events.ToArray() } };
    }
    float CharacterIndex(string id) =>
        (Array.FindIndex(sim.Data.AllyDefinitions, d => d.Id == id) + 1f) /
        Math.Max(1, sim.Data.AllyDefinitions.Length);
    object Observe()
    {
        var values = new List<float>();
        var r = sim.Data.Rules;
        int unitCapacity = Math.Max(1, maxUnits);
        values.AddRange([sim.Time / r.EpisodeSeconds, (float)sim.Energy[0].Current / r.MaxEnergy,
            sim.Energy[0].Progress, (float)AllySquads().Length / squadLimit,
            (float)draftDepth / MaxDraft, (float)selected.Count / handSize,
            (float)Math.Max(0, destination % sim.Grid.Width) / width,
            (float)Math.Max(0, destination / sim.Grid.Width) / height,
            selectedSquad >= 0 ? 1 : 0, (float)sim.Deck.Pool.Count / Math.Max(1, sim.Deck.Characters.Length)]);
        var forecast = sim.Forecast;
        var forecastBase = forecast == null ? null : sim.Bases.First(b => b.Id == forecast.Origin);
        values.AddRange([forecast == null ? 0 : 1,
            forecast == null ? 0 : forecast.Remaining / r.EpisodeSeconds,
            forecast == null ? 0 : (float)forecast.Characters.Count / unitCapacity,
            forecastBase == null ? 0 : (float)forecastBase.Definition.Cell[0] / width,
            forecastBase == null ? 0 : (float)forecastBase.Definition.Cell[1] / height]);
        for (int i = 0; i < handSize; i++)
        {
            if (i >= sim.Deck.Hand.Count) { values.AddRange(new float[CardFeatures]); continue; }
            int card = sim.Deck.Hand[i]; var d = sim.Definition(sim.Deck.Characters[card], 0);
            values.AddRange([1, CharacterIndex(d.Id), (float)d.Cost / r.MaxEnergy,
                d.Hp / (float)sim.Data.AllyDefinitions.Max(d => d.Hp),
                d.Power / (float)sim.Data.AllyDefinitions.Max(d => d.Power),
                d.Speed / sim.Data.AllyDefinitions.Max(d => d.Speed), selected.Contains(card) ? 1 : 0]);
        }
        for (int i = 0; i < maxBases; i++)
        {
            if (i >= sim.Bases.Count) { values.AddRange(new float[BaseFeatures]); continue; }
            var b = sim.Bases[i];
            values.AddRange([1, b.Team, (float)Math.Max(0, b.Hp) / b.MaximumHp,
                b.Transform.position.x / (width * GameConfig.cellSize),
                b.Transform.position.z / (height * GameConfig.cellSize),
                b.Definition.Main ? 1 : 0, b.Definition.Boss ? 1 : 0, origin == b.Id ? 1 : 0]);
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
                (float)(u.Destination % sim.Grid.Width) / width,
                (float)(u.Destination / sim.Grid.Width) / height,
                u.Cooldown / r.EpisodeSeconds, u.Windup / r.EpisodeSeconds,
                u.Squad == selectedSquad ? 1 : 0, (float)u.Order / unitCapacity]);
            foreach (var id in StatusEffectIdResolver.All)
                values.Add(u.Status.RemainingSeconds(id) / r.EpisodeSeconds);
        }
        for (int i = 0; i < width * height; i++)
        {
            int cell = GridCell(CellStart + i);
            values.Add(cell >= 0 && sim.Grid.Walkable(cell) ? 1 : 0);
            values.Add(cell >= 0 && sim.Grid.BaseCells.Contains(cell) ? 1 : 0);
        }
        var squads = AllySquads();
        for (int i = 0; i < squadLimit; i++)
        {
            if (i >= squads.Length) { values.AddRange(new float[SquadFeatures]); continue; }
            var s = squads[i];
            values.AddRange([1, sim.Units.Count(u => u.Squad == s.Id) / (float)unitCapacity,
                (float)(s.Destination % sim.Grid.Width) / width,
                (float)(s.Destination / sim.Grid.Width) / height, s.Id == selectedSquad ? 1 : 0]);
        }
        // Append defense channels so the established card-selection offsets remain stable.
        for (int i = 0; i < handSize; i++)
        {
            int defense = i < sim.Deck.Hand.Count
                ? sim.Definition(sim.Deck.Characters[sim.Deck.Hand[i]], BattleSimulation.Ally).Defense : 0;
            values.Add((float)(defense / (CombatDamageCalculator.DefenseScale + defense)));
        }
        for (int i = 0; i < maxUnits; i++)
        {
            int defense = i < units.Length ? units[i].Defense : 0;
            values.Add((float)(defense / (CombatDamageCalculator.DefenseScale + defense)));
        }
        return new { values, action_mask = Mask() };
    }
    public object Save() => new { engine_hash = engineHash, scenario, seed, actions = history.ToArray(),
        debug = new { tick = sim.Tick, outcome = sim.Outcome, energy = sim.Energy.Select(e => e.Current).ToArray(),
            hand = sim.Deck.Hand.ToArray(), pool = sim.Deck.Pool.ToArray(),
            units = sim.Units.Select(u => new { u.Id, u.Team, character = u.Definition.Id, u.Hp, u.Defense,
                x = u.Transform.position.x, z = u.Transform.position.z, u.Spawned, u.Squad, u.Order,
                u.Destination, u.Attacking, u.Cooldown, u.Windup,
                statuses = u.Status.Active.Select(id => new { id = (int)id, seconds = u.Status.RemainingSeconds(id) }).ToArray() }).ToArray(),
            bases = sim.Bases.Select(b => new { b.Id, b.Team, b.Hp }).ToArray(),
            metrics = sim.Metrics() } };
    public object Load(JsonElement state)
    {
        if (state.GetProperty("engine_hash").GetString() != engineHash) throw new ArgumentException("Engine changed");
        Reset(state.GetProperty("scenario"), state.GetProperty("seed").GetInt32());
        foreach (var action in state.GetProperty("actions").EnumerateArray()) Step(action.GetInt32());
        return Observe();
    }
}
