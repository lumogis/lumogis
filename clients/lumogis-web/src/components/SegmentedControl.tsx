// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

export interface SegmentedOption {
  value: string;
  label: string;
  disabled?: boolean;
  title?: string;
}

export interface SegmentedControlProps {
  options: ReadonlyArray<SegmentedOption>;
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
}

export function SegmentedControl({
  options,
  value,
  onChange,
  ariaLabel,
  className,
}: SegmentedControlProps): JSX.Element {
  return (
    <div
      className={["lumogis-segment", className].filter(Boolean).join(" ")}
      role="group"
      aria-label={ariaLabel}
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className="lumogis-segment__btn"
          aria-pressed={value === opt.value}
          disabled={opt.disabled}
          title={opt.title}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
