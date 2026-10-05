using UnityEngine;

/// <summary>
/// 状態1種の定義。仕様書§20「調整値はデータ定義へ集約する」に従い、
/// 効果時間と見た目の値をここへ集める。処理側は数値を直接持たない。
/// 体表への配置密度・倍率は status_* のスペックJSONが持つ(EffectStudioと共有するため)。
/// </summary>
[CreateAssetMenu(
    fileName = "StatusDefinition",
    menuName = "ScriptableObjects/StatusDefinition",
    order = 6)]
public sealed class StatusDefinition : ScriptableObject
{
    const float MinimumDurationSeconds = 0.1f;
    const float DefaultDurationSeconds = 6f;
    /// <summary>ダメージを持たない状態(朦朧・冷却・草付着・粘液・光蝕・呪い)の値。</summary>
    const int NoDamagePerTick = 0;
    const float DefaultRimGlowStrength = 0.35f;

    [SerializeField] StatusEffectId statusId = StatusEffectId.Poison;
    [SerializeField] string displayName = "";
    [Tooltip("シルエット外周と体表モチーフの基調色。モチーフのパレットと揃える")]
    [SerializeField] Color statusColor = Color.white;
    [Min(MinimumDurationSeconds)]
    [Tooltip("付与時に設定する効果時間。再付与はこの値へ戻し、加算しない")]
    [SerializeField] float durationSeconds = DefaultDurationSeconds;
    [Tooltip("多重付与時の表示順。小さいほど優先して見せる")]
    [SerializeField] int displayPriority;
    [Tooltip("体表へ貼るモチーフのEffect名(Resources/VoxelEffects/<name>.json)")]
    [SerializeField] string surfaceMotifEffectName = "";
    [Min(NoDamagePerTick)]
    [Tooltip("刻むごとに与えるダメージ。0ならダメージを持たない状態")]
    [SerializeField] int damagePerTick = NoDamagePerTick;
    [Range(0f, 1f)]
    [Tooltip("シルエット輪郭の太さ係数。既存アセット互換のためフィールド名は維持する")]
    [SerializeField] float rimGlowStrength = DefaultRimGlowStrength;

    public StatusEffectId StatusId => statusId;
    public string DisplayName => string.IsNullOrWhiteSpace(displayName)
        ? statusId.ToString()
        : displayName;
    public Color StatusColor => statusColor;
    public float DurationSeconds => Mathf.Max(MinimumDurationSeconds, durationSeconds);
    public int DisplayPriority => displayPriority;
    /// <summary>刻むごとに与えるダメージ。</summary>
    public int DamagePerTick => Mathf.Max(NoDamagePerTick, damagePerTick);
    /// <summary>状態ダメージを持つか。持たない状態は刻んでも何も起きない。</summary>
    public bool DealsDamage => DamagePerTick > NoDamagePerTick;
    public string SurfaceMotifEffectName => surfaceMotifEffectName;
    public float OutlineStrength => Mathf.Clamp01(rimGlowStrength);
    /// <summary>既存コード互換。新規コードでは OutlineStrength を使う。</summary>
    public float RimGlowStrength => OutlineStrength;

    /// <summary>生成器・エディタ拡張から初期値を書き込むための入口。</summary>
    public void Configure(
        StatusEffectId configuredStatusId,
        string configuredDisplayName,
        Color configuredStatusColor,
        float configuredDurationSeconds,
        int configuredDisplayPriority,
        string configuredSurfaceMotifEffectName,
        float configuredRimGlowStrength,
        int configuredDamagePerTick)
    {
        statusId = configuredStatusId;
        displayName = configuredDisplayName;
        statusColor = configuredStatusColor;
        durationSeconds = Mathf.Max(MinimumDurationSeconds, configuredDurationSeconds);
        displayPriority = configuredDisplayPriority;
        surfaceMotifEffectName = configuredSurfaceMotifEffectName;
        rimGlowStrength = Mathf.Clamp01(configuredRimGlowStrength);
        damagePerTick = Mathf.Max(NoDamagePerTick, configuredDamagePerTick);
    }

    void OnValidate()
    {
        durationSeconds = Mathf.Max(MinimumDurationSeconds, durationSeconds);
        rimGlowStrength = Mathf.Clamp01(rimGlowStrength);
        damagePerTick = Mathf.Max(NoDamagePerTick, damagePerTick);
    }
}
