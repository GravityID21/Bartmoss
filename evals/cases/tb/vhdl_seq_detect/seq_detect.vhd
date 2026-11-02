-- Detects the bit pattern 1011 on din, sampled on rising clk edges, with overlapping matches
-- allowed (1011011 contains two). found is high for the one cycle after the edge that sampled the
-- pattern's last bit. Synchronous active-high reset.
library ieee;
use ieee.std_logic_1164.all;

entity seq_detect is
  port (
    clk   : in  std_logic;
    rst   : in  std_logic;
    din   : in  std_logic;
    found : out std_logic
  );
end entity;

architecture rtl of seq_detect is
  type state_t is (S0, S1, S10, S101, S1011);
  signal st : state_t;
begin
  process (clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        st <= S0;
      else
        case st is
          when S0    => if din = '1' then st <= S1; else st <= S0; end if;
          when S1    => if din = '1' then st <= S1; else st <= S10; end if;
          when S10   => if din = '1' then st <= S101; else st <= S0; end if;
          when S101  => if din = '1' then st <= S1011; else st <= S10; end if;
          when S1011 => if din = '1' then st <= S1; else st <= S10; end if;
        end case;
      end if;
    end if;
  end process;

  found <= '1' when st = S1011 else '0';
end architecture;
