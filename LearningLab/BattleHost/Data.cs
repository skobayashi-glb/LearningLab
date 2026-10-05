using System.Reflection;
using System.Text.Json;
using UnityEngine;

namespace BattleHost;

public static class Wire
{
    public const int Version = 1;
    public static readonly JsonSerializerOptions Options = new()
    { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, PropertyNameCaseInsensitive = true,
      IncludeFields = true };
    public static T Read<T>(JsonElement element) => element.Deserialize<T>(Options);
    public static void ApplySerializedFields(object target, JsonElement data)
    {
        foreach (var field in target.GetType().GetFields(
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
            if (data.TryGetProperty(field.Name, out var value))
                field.SetValue(target, field.FieldType == typeof(bool) && value.ValueKind == JsonValueKind.Number
                    ? value.GetInt32() != 0 : JsonSerializer.Deserialize(value.GetRawText(), field.FieldType, Options));
    }
}
[System.Text.Json.Serialization.JsonUnmappedMemberHandling(System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow)]
public sealed class Rules
{
    public float TickSeconds { get; set; } = 1f / 60;
    public float DecisionSeconds { get; set; } = 0.5f;
    public float EpisodeSeconds { get; set; } = 180;
    public int InitialEnergy { get; set; } = 5;
    public int MaxEnergy { get; set; } = 20;
    public float EnergyRecoverySeconds { get; set; } = 3;
    public int HandSize { get; set; } = 8;
    public int SquadLimit { get; set; } = 5;
    public int DiscardCost { get; set; } = 1;
    public float EnemyDecisionSeconds { get; set; } = 4;
    public float EnemyForecastSeconds { get; set; } = 3;
    public float EnemySpendMinimum { get; set; } = 0.3f;
    public float EnemyAlertBonus { get; set; } = 0.25f;
    public int EnemyDuplicateLimit { get; set; } = 3;
    public int EnemyInitialEnergy { get; set; } = 5;
    public bool EnemyEnabled { get; set; } = true;
    public bool ShuffleDeck { get; set; } = true;
    public float ContactMarginCells { get; set; } = 0.02f;
    public float SeparationCells { get; set; } = 0.35f;
    public float ChainMaximumCells { get; set; } = 1.5f;
    public float TurnDegreesPerSecond { get; set; } = 360;
    public float AttackFacingDegrees { get; set; } = 20;
    public float WinReward { get; set; } = 1;
    public float LossReward { get; set; } = -1;
    public float HpRewardWeight { get; set; } = 0.1f;
}
public sealed class UnitDefinition
{
    public string Id { get; set; }
    public int Hp { get; set; }
    public int Power { get; set; }
    public int Defense { get; set; } = CombatDamageCalculator.MinimumDefense;
    public int Cost { get; set; }
    public float Speed { get; set; }
    public float ImpactSeconds { get; set; }
    public float RecoverySeconds { get; set; }
    public float Range { get; set; }
    public float Lunge { get; set; }
    public bool Ranged { get; set; }
    public bool IgnoreSight { get; set; }
    public int MoveType { get; set; }
    public int TargetSelection { get; set; }
    public int MaxTargets { get; set; }
    public BodyBox Body { get; set; }
    public AttackShape Shape { get; set; }
    public EngagementRange Engagement { get; set; }
    public int[] Statuses { get; set; } = [];
    public float[] KnockbackHpRatios { get; set; } = [];
    public string SourceHash { get; set; } = "";
}
public sealed class BaseDefinition
{
    public string Id { get; set; }
    public int Team { get; set; }
    public bool Main { get; set; }
    public bool Boss { get; set; }
    public int Hp { get; set; }
    public int[] Cell { get; set; }
    public int[][] Footprint { get; set; } = [];
    public int[][] SpawnCells { get; set; } = [];
    public string[] Garrison { get; set; } = [];
}
public sealed class StageData
{
    public string Id { get; set; }
    public int Width { get; set; }
    public int Height { get; set; }
    public int[] Terrain { get; set; }
    public int GenerationSeed { get; set; }
    public float WallCandidatePathRate { get; set; }
    public BaseDefinition[] Bases { get; set; }
}
public sealed class InitialUnit
{
    public int Team { get; set; }
    public string Character { get; set; }
    public int[] Cell { get; set; }
    public int[] Destination { get; set; }
}
public sealed class BattleData
{
    public int Version { get; set; } = 1;
    public float CellSize { get; set; } = 3.2f;
    public StageData Stage { get; set; }
    public Rules Rules { get; set; } = new();
    public UnitDefinition[] AllyDefinitions { get; set; }
    public UnitDefinition[] EnemyBaseline { get; set; }
    public string[] Roster { get; set; }
    public string[] EnemyPool { get; set; }
    public InitialUnit[] InitialUnits { get; set; } = [];
    public JsonElement[] StatusDefinitions { get; set; } = [];
    public JsonElement[] ReactionDefinitions { get; set; } = [];
    public JsonElement StatusSettings { get; set; }
}
public sealed class EnergyWallet(int initial, int maximum, float interval)
{
    public int Current { get; private set; } = initial;
    public float Progress { get; private set; }
    public int Spent { get; private set; }
    public void Tick(float seconds, float multiplier = 1)
    {
        if (Current >= maximum) { Progress = 0; return; }
        Progress += seconds * multiplier / interval;
        int recovered = (int)Progress;
        Current = Math.Min(maximum, Current + recovered);
        Progress = Current == maximum ? 0 : Progress - recovered;
    }
    public void Spend(int amount)
    {
        if (amount <= 0 || amount > Current) throw new InvalidOperationException("Insufficient energy");
        Current -= amount;
        Spent += amount;
    }
}
public sealed class SortieDeck
{
    public List<int> Hand { get; } = [];
    public List<int> Pool { get; } = [];
    public string[] Characters { get; }
    readonly int handSize;
    public SortieDeck(string[] characters, int size, Random random, bool shuffle)
    {
        Characters = characters;
        handSize = size;
        Pool.AddRange(Enumerable.Range(0, characters.Length));
        if (shuffle)
            for (int i = Pool.Count - 1; i > 0; i--)
            { int j = random.Next(i + 1); (Pool[i], Pool[j]) = (Pool[j], Pool[i]); }
        Refill();
    }
    public void Remove(IEnumerable<int> cards)
    { foreach (int card in cards) if (!Hand.Remove(card)) throw new InvalidOperationException("Card not in hand"); }
    public void Return(int card) { Pool.Add(card); Refill(); }
    public void Refill()
    { while (Hand.Count < handSize && Pool.Count > 0) { Hand.Add(Pool[0]); Pool.RemoveAt(0); } }
}
