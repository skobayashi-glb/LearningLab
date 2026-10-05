using System;

/// <summary>防御を持つ対象だけが実装する。拠点・配置物へ防御値を強制しない。</summary>
public interface IDefenseTarget
{
    int Defense { get; }
}

public enum CombatDamageKind
{
    Direct,
    Reaction,
    Status,
}

/// <summary>
/// Unityと独立シミュレーターで共有する、防御力による最終ダメージ解決。
/// 状態倍率を適用済みの値を受け取り、通常攻撃だけを軽減する。
/// 状態異常・状態反応のダメージは防御を無視する。
/// </summary>
public static class CombatDamageCalculator
{
    public const int MinimumDefense = 0;
    public const int NoDamage = 0;
    public const int MinimumPositiveDamage = 1;
    public const double DefenseScale = 100d;

    public static int Resolve(int modifiedDamage, int defense, CombatDamageKind kind)
    {
        if (modifiedDamage <= NoDamage) return NoDamage;
        switch (kind)
        {
            case CombatDamageKind.Status:
            case CombatDamageKind.Reaction:
                return modifiedDamage;
            case CombatDamageKind.Direct:
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(kind));
        }
        int normalizedDefense = Math.Max(MinimumDefense, defense);
        double reduced = modifiedDamage * DefenseScale / (DefenseScale + normalizedDefense);
        return Math.Max(MinimumPositiveDamage,
            (int)Math.Round(reduced, MidpointRounding.AwayFromZero));
    }
}
