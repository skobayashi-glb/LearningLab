/// <summary>基礎状態の組み合わせから発生する状態反応。</summary>
public enum StatusReactionId
{
    Infection,
    Venom,
    Stun,
    Charge,
    Burst,
    Cauterize,
    Frostbite,
    IceShatter,
    Combustion,
    IceBind,
    CurseErosion,
}

/// <summary>反応の成立に付与順を要求するか。</summary>
public enum StatusReactionTriggerRule
{
    AnyOrder,
    PrerequisiteThenTrigger,
}

/// <summary>反応後に残る機械的な効果の寿命。</summary>
public enum StatusReactionLifetimePolicy
{
    Instant,
    FixedDuration,
    WhileCarrierStatusActive,
    WhileBothStatusesActive,
}
