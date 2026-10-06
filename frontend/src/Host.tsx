type Props = { state: "idle" | "thinking" | "tipping" };

export function Host({ state }: Props) {
  return (
    <span className={`host-figure host-${state}`}>
      <svg className="host-svg" viewBox="0 0 120 168" role="img" aria-label="The host">
        <path className="ink-hatch host-ground" d="M26 164 H94" />
        <path className="ink-fill" d="M47 148 L47 158 L58 158 L58 148 Z M62 148 L62 158 L73 158 L73 148 Z" />
        <ellipse className="ink-fill" cx="52" cy="159.5" rx="7" ry="2.6" />
        <ellipse className="ink-fill" cx="68" cy="159.5" rx="7" ry="2.6" />
        <path
          className="ink-fill"
          d="M42 92 C41 108 43 120 44 130 C44 138 43 144 42 149 L78 149 C77 144 76 138 76 130 C77 120 79 108 78 92 Q60 86 42 92 Z"
        />
        <path className="paper-fill host-shirt" d="M52 93 L60 116 L68 93 Q60 89 52 93 Z" />
        <path className="paper-line" d="M48 95 L57 112 M72 95 L63 112" />
        <circle className="paper-dot" cx="60" cy="124" r="1.3" />
        <circle className="paper-dot" cx="60" cy="132" r="1.3" />
        <path className="host-band" d="M69 103 L75 100 L75.5 106 Z" />
        <g className="host-arm host-arm-near">
          <path className="ink-fill" d="M42 93 C36 99 32 108 32 118 L38 119 C39 111 41 105 45 100 Z" />
          <path className="ink-fill" d="M32 118 C32 124 37 128 43 128 L44 122 C40 122 38 120 38 118 Z" />
          <path className="paper-line" d="M45 96 C41.5 103 40 110 39 118" />
          <path
            className="paper-fill"
            d="M40 120 C41 117 46 116.5 48.5 119 C50.5 122 49.5 128 45 128.5 C41 129 39.5 124 40 120 Z"
          />
        </g>
        <g className="host-arm">
          <path className="ink-fill" d="M78 93 C86 98 90 108 90 119 L84 121 C84 112 81 104 76 100 Z" />
          <path className="paper-line" d="M76.5 96 C80.5 102 83.5 110 84 120" />
          <path className="host-cane" d="M88 117 L92 163" />
          <circle className="paper-fill" cx="88" cy="116.5" r="2.6" />
          <path
            className="paper-fill"
            d="M83 122 C83 118.5 86.5 117.5 89.5 119 C92.5 120.5 93 125 91 127.5 C88.5 130 84 128 83 125 Z"
          />
        </g>
        <path className="paper-fill" d="M55.5 80 L55 89 L65 89 L64.5 80 Z" />
        <circle className="paper-fill host-head" cx="60" cy="63" r="20" />
        <path className="ink-hatch" d="M43 70 l3.5 5 M45 75 l3.5 4.5 M48 79.5 l3.5 3" />
        <g className="host-eye" transform="translate(51 62)">
          <g className="host-eye-comma">
            <circle className="host-glyph" r="3.2" />
            <path className="host-glyph" d="M2.8 1.8 C2.5 4.8 0.7 7.1 -1.9 8.4 C0.3 5.8 1.3 3.5 1.3 1.3 Z" />
          </g>
        </g>
        <g className="host-eye" transform="translate(69 62)">
          <g className="host-eye-star">
            <path className="host-stroke" d="M0 -5 V5 M-4.4 -2.5 L4.4 2.5 M-4.4 2.5 L4.4 -2.5" />
          </g>
        </g>
        <path className="host-lid" d="M45 62 H57 M63 62 H75" />
        <path className="host-bow" d="M60 92 L52.5 88.5 L52.5 95.5 Z M60 92 L67.5 88.5 L67.5 95.5 Z" />
        <circle className="host-bow" cx="60" cy="92" r="2" />
        <g className="host-hat">
          <path className="ink-fill" d="M42 46 L44 14 Q44 10 48 10 L72 10 Q76 10 76 14 L78 46 Z" />
          <path className="host-band" d="M42.5 38 L77.5 38 L78 44 L42 44 Z" />
          <path className="ink-fill" d="M30 47.5 Q60 39 90 47.5 Q60 56 30 47.5 Z" />
          <path className="paper-hatch" d="M49 14 v21 M53.5 12.5 v23 M58 12 v24" />
        </g>
      </svg>
    </span>
  );
}
