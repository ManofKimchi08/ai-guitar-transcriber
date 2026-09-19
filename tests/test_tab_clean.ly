\version "2.24.4"

guitarMusic = \relative c' {
  \numericTimeSignature\time 4/4
  e4\1 b4\2 g4\3 d4\4 |
  a,4\5 e,4\6 <e, b, e g b e'>2 |
}

bassMusic = \relative c {
  \numericTimeSignature\time 4/4
  e,4\4 a,4\3 d4\2 g4\1 |
}

\score {
  <<
    \new TabStaff \with {
      stringTunings = #guitar-tuning
      instrumentName = "Guitar TAB"
    } {
      \guitarMusic
    }
    \new TabStaff \with {
      stringTunings = #bass-tuning
      instrumentName = "Bass TAB"
    } {
      \bassMusic
    }
  >>
  \layout {
    \context {
      \Score
      \omit ClefModifier
    }
  }
}
