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
    public int MaxEnergy { get; set; } = 30;
    public float EnergyRecoverySeconds { get; set; } = PartyControls.EnergyRecoverySeconds;
    public int PartySize { get; set; } = PartyControls.PartySize;
    public int RouteCount { get; set; } = PartyControls.RouteCount;
    public int MaximumWaypoints { get; set; } = PartyControls.MaximumWaypoints;
    public int EnemySquadLimit { get; set; } = PartyControls.EnemySquadLimit;
    public float EnemyDecisionSeconds { get; set; } = 4;
    public float EnemyForecastSeconds { get; set; } = 3;
    public float EnemySpendMinimum { get; set; } = 0.3f;
    public float EnemyAlertBonus { get; set; } = 0.25f;
    public int EnemyDuplicateLimit { get; set; } = 3;
    public int EnemyInitialEnergy { get; set; } = 5;
    public bool EnemyEnabled { get; set; } = true;
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
    public const int CurrentVersion = 2;
    public int Version { get; set; } = CurrentVersion;
    public float CellSize { get; set; } = 3.2f;
    public StageData Stage { get; set; }
    public Rules Rules { get; set; } = new();
    public UnitDefinition[] AllyDefinitions { get; set; }
    public UnitDefinition[] EnemyBaseline { get; set; }
    public string[] Roster { get; set; }
    public string[] EnemyPool { get; set; }
    public InitialUnit[] InitialUnits { get; set; } = [];
    public OpeningWave OpeningWave { get; set; }
    public JsonElement[] StatusDefinitions { get; set; } = [];
    public JsonElement[] ReactionDefinitions { get; set; } = [];
    public JsonElement StatusSettings { get; set; }
}
public sealed class OpeningWave
{
    public string[] Ally { get; set; } = [];
    public string[] Enemy { get; set; } = [];
}
public sealed class EnergyWallet(int initial, int maximum, float interval)
{
    const double RecoveryTolerance = 0.000001;
    double recoveryProgress;
    public int Current { get; private set; } = initial;
    public float Progress => (float)recoveryProgress;
    public int Spent { get; private set; }
    public void Tick(float seconds, float multiplier = 1)
    {
        if (Current >= maximum) { recoveryProgress = 0; return; }
        recoveryProgress += (double)seconds * multiplier / interval;
        int recovered = (int)(recoveryProgress + RecoveryTolerance);
        Current = Math.Min(maximum, Current + recovered);
        recoveryProgress = Current == maximum ? 0 : Math.Max(0, recoveryProgress - recovered);
    }
    public void Spend(int amount)
    {
        if (amount <= 0 || amount > Current) throw new InvalidOperationException("Insufficient energy");
        Current -= amount;
        Spent += amount;
    }
}
public static class PartyControls
{
    public const int PartySize = 12, RouteCount = 3, MaximumWaypoints = 2, EnemySquadLimit = 5;
    public const float EnergyRecoverySeconds = 1;
}
public sealed class FixedPartyRoster
{
    public IReadOnlyList<string> Characters { get; }
    public FixedPartyRoster(string[] characters, int capacity)
    {
        if (characters == null || characters.Length == 0 || characters.Length > capacity ||
            characters.Distinct().Count() != characters.Length)
            throw new ArgumentException("Fixed party must contain unique characters within party_size; re-export legacy decks");
        Characters = Array.AsReadOnly((string[])characters.Clone());
    }
}
