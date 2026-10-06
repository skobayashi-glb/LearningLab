using UnityEngine;

namespace BattleHost;

public abstract class BattleTarget : IDamageTarget
{
    public int Id;
    public int Team;
    public int Hp;
    public int MaximumHp;
    public Transform Transform { get; } = new();
    public abstract BodyBox BodyBox { get; }
    public bool Alive => Hp > 0;
}
public sealed class BattleBase : BattleTarget, ICombatFootprintTarget
{
    public BaseDefinition Definition;
    public int[] SpawnIndices;
    public override BodyBox BodyBox => BodyBox.Default;
    public IReadOnlyList<Vector2Int> OccupiedCells =>
        Definition.Footprint.Select(c => new Vector2Int(c[0], c[1])).ToArray();
}
public sealed class BattleUnit : BattleTarget, IDefenseTarget
{
    public UnitDefinition Definition;
    public int Defense => Definition.Defense;
    public int Route = -1;
    public int[] Path = [];
    public int PathProgress;
    public int Squad = -1;
    public int Order;
    public int Home;
    public int Destination;
    public bool Spawned;
    public float Cooldown;
    public float Windup;
    public int TargetId = -1;
    public bool Attacking;
    public AttackAim Aim;
    public float AliveSeconds;
    public float DamageDone;
    public float BlockedSeconds;
    public UnitStatusState Status { get; } = new();
    public UnitStatusReactionState Reactions { get; } = new();
    public Dictionary<int, float> DotElapsed { get; } = [];
    public Dictionary<int, int> StatusSources { get; } = [];
    public float CurseReady;
    public override BodyBox BodyBox => Definition.Body;
}
public sealed class Squad
{
    public int Id;
    public int Team;
    public int Destination;
    public int Origin;
}
public sealed class EnemyForecast
{
    public float Remaining;
    public int Origin;
    public int Destination;
    public List<string> Characters = [];
}
public record BattleEvent(int Tick, string Kind, int Source, int Target, float Amount, string Detail);
public record DamageIntent(int Source, int Target, int Damage, string Kind, int[] Statuses);
