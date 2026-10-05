using System.Collections.Generic;

/// <summary>
/// 技が対象へ付与できる基礎状態。仕様書§10.3の分類のうち、
/// MotionStudioから直接選べる11種だけを持つ。
/// 派生状態(燃焼・猛毒・気絶・氷縛)は状態反応の実装時に別で扱う。
/// </summary>
public enum StatusEffectId
{
    Poison,
    Electric,
    Burn,
    Bleed,
    Sanded,
    Dazed,
    Chill,
    Grass,
    Slime,
    Lumen,
    Curse,
}

/// <summary>
/// MotionStudio/キャラクターカタログの保存ID文字列と StatusEffectId の相互変換。
/// 文字列表現をここだけに閉じ込め、他の処理はenumだけを扱う。
/// </summary>
public static class StatusEffectIdResolver
{
    /// <summary>保存ID(technique-status-system-spec-revised.md §4)→ enum。</summary>
    static readonly Dictionary<string, StatusEffectId> IdsByStorageName =
        new Dictionary<string, StatusEffectId>(System.StringComparer.Ordinal)
        {
            { "poison", StatusEffectId.Poison },
            { "electric", StatusEffectId.Electric },
            { "burn", StatusEffectId.Burn },
            { "bleed", StatusEffectId.Bleed },
            { "sanded", StatusEffectId.Sanded },
            { "dazed", StatusEffectId.Dazed },
            { "chill", StatusEffectId.Chill },
            { "grass", StatusEffectId.Grass },
            { "slime", StatusEffectId.Slime },
            { "lumen", StatusEffectId.Lumen },
            { "curse", StatusEffectId.Curse },
        };

    static readonly Dictionary<StatusEffectId, string> StorageNamesById =
        BuildStorageNames();

    /// <summary>全状態を宣言順で列挙する。カタログの初期化と検証で使う。</summary>
    public static readonly IReadOnlyList<StatusEffectId> All = new[]
    {
        StatusEffectId.Poison,
        StatusEffectId.Electric,
        StatusEffectId.Burn,
        StatusEffectId.Bleed,
        StatusEffectId.Sanded,
        StatusEffectId.Dazed,
        StatusEffectId.Chill,
        StatusEffectId.Grass,
        StatusEffectId.Slime,
        StatusEffectId.Lumen,
        StatusEffectId.Curse,
    };

    /// <summary>
    /// 保存IDを解決する。レガシーID(exposed / confusion)は
    /// Tools/sync-character-catalog.js が移行済みなので、ここでは受け付けない。
    /// </summary>
    public static bool TryResolve(string storageName, out StatusEffectId statusId)
    {
        if (string.IsNullOrWhiteSpace(storageName))
        {
            statusId = default;
            return false;
        }
        return IdsByStorageName.TryGetValue(storageName.Trim(), out statusId);
    }

    public static string ToStorageName(StatusEffectId statusId)
    {
        return StorageNamesById.TryGetValue(statusId, out string storageName)
            ? storageName
            : statusId.ToString();
    }

    static Dictionary<StatusEffectId, string> BuildStorageNames()
    {
        var storageNames = new Dictionary<StatusEffectId, string>();
        foreach (KeyValuePair<string, StatusEffectId> entry in IdsByStorageName)
        {
            storageNames[entry.Value] = entry.Key;
        }
        return storageNames;
    }
}
