-- PWM generator. A free-running counter counts 0 .. PERIOD-1. pwm_out is high while the
-- counter is below the latched duty value, so duty = 0 gives a constant low output and
-- duty >= PERIOD a constant high output. `duty` is latched as the counter wraps, so a new
-- value takes effect at the start of the next period. After reset the latched duty is 0.
-- period_start is high in the first cycle of every period (counter = 0).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity pwm is
    generic (
        PERIOD : positive := 16;
        WIDTH  : positive := 8
    );
    port (
        clk          : in  std_logic;
        rst_n        : in  std_logic;
        duty         : in  unsigned(WIDTH - 1 downto 0);
        pwm_out      : out std_logic;
        period_start : out std_logic
    );
end entity;

architecture rtl of pwm is
    signal count  : natural range 0 to PERIOD - 1;
    signal duty_q : unsigned(WIDTH - 1 downto 0);
begin
    process (clk, rst_n)
    begin
        if rst_n = '0' then
            count  <= 0;
            duty_q <= (others => '0');
        elsif rising_edge(clk) then
            if count = PERIOD - 1 then
                count  <= 0;
                duty_q <= duty;
            else
                count <= count + 1;
            end if;
        end if;
    end process;

    pwm_out      <= '1' when count < to_integer(duty_q) else '0';
    period_start <= '1' when count = 0 else '0';
end architecture;
