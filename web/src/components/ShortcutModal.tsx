import { X } from "lucide-react";

interface ShortcutModalProps {
  open: boolean;
  onClose: () => void;
}

const SHORTCUT_ITEMS: Array<{ keys: string[]; desc: string }> = [
  { keys: ["j", "↓"], desc: "聚焦下一张情报卡片" },
  { keys: ["k", "↑"], desc: "聚焦上一张情报卡片" },
  { keys: ["m"], desc: "切换当前卡片已读状态并折叠" },
  { keys: ["o", "回车"], desc: "在新标签页中打开当前卡片原文" },
  { keys: ["s"], desc: "临时解锁或重新遮蔽当前卡片剧透" },
  { keys: ["g"], desc: "打开或关闭历史术语本弹窗" },
  { keys: ["1-4"], desc: "快速切换上方领域分类标签" },
  { keys: ["?"], desc: "打开或关闭此快捷键说明弹窗" },
  { keys: ["退出"], desc: "关闭弹窗" },
];

export function ShortcutModal({ open, onClose }: ShortcutModalProps) {
  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="键盘快捷键说明"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bento-surface w-full max-w-md rounded-2xl p-5 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-white/[0.07] pb-3">
          <div>
            <h2 className="text-sm font-semibold text-[#F3F4F6]">
              键盘优先阅读快捷键
            </h2>
            <p className="mt-0.5 text-xs text-[#6B7280]">
              无需鼠标，2 分钟快速清空今日情报流
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="rounded-lg border border-white/10 bg-white/[0.03] p-1.5 text-[#9CA3AF] transition-colors hover:bg-white/[0.08] hover:text-[#F3F4F6] cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <ul className="mt-4 space-y-2.5">
          {SHORTCUT_ITEMS.map((item) => (
            <li
              key={item.desc}
              className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.02] px-3 py-2 text-xs"
            >
              <span className="text-[#D1D5DB]">{item.desc}</span>
              <span className="flex shrink-0 items-center gap-1">
                {item.keys.map((keyLabel) => (
                  <kbd key={keyLabel} className="kbd-cap">
                    {keyLabel}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
