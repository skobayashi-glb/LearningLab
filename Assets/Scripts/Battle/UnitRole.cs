using System;
using UnityEngine;

/// <summary>
/// キャラクターの戦術上の役割。複数を兼ねられるのでフラグで持つ。
/// 接近方式(AttackApproachType)は「接敵の物理挙動」、こちらは「部隊での役割」を表す別軸。
/// </summary>
[Flags]
public enum UnitRole
{
    [InspectorName("未設定")] None = 0,
    [InspectorName("アタッカー")] Attacker = 1 << 0,
    [InspectorName("タンク")] Tank = 1 << 1,
    [InspectorName("ヒーラー")] Healer = 1 << 2,
    [InspectorName("バッファー")] Buffer = 1 << 3,
    [InspectorName("状態付与")] StatusInflictor = 1 << 4,
    [InspectorName("反応完成")] ReactionFinisher = 1 << 5,
    [InspectorName("位置操作")] Positioning = 1 << 6,
}

/// <summary>
/// UnitRole の集合演算と分類をここへ集める。
/// 「サポーター」は別挙動にする方針なので、どの役割がサポーターに当たるかを一箇所で定義する。
/// </summary>
public static class UnitRoleSet
{
    /// <summary>サポーター扱いにする役割。別挙動の判定はこの集合だけを見る。</summary>
    public const UnitRole SupporterRoles = UnitRole.Healer | UnitRole.Buffer;

    /// <summary>指定の役割をすべて含むか。</summary>
    public static bool Contains(UnitRole roles, UnitRole required)
    {
        return required != UnitRole.None && (roles & required) == required;
    }

    /// <summary>指定の役割のどれかを含むか。</summary>
    public static bool ContainsAny(UnitRole roles, UnitRole candidates)
    {
        return (roles & candidates) != UnitRole.None;
    }

    /// <summary>ヒーラー・バッファーのどちらかを持つキャラはサポーターとして別挙動にする。</summary>
    public static bool IsSupporter(UnitRole roles)
    {
        return ContainsAny(roles, SupporterRoles);
    }
}
