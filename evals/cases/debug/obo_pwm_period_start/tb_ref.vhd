library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_ref is
end entity;

architecture sim of tb_ref is
    constant PERIOD : positive := 8;
    signal clk          : std_logic := '0';
    signal rst_n        : std_logic := '0';
    signal duty         : unsigned(3 downto 0) := (others => '0');
    signal pwm_out      : std_logic;
    signal period_start : std_logic;
begin
    clk <= not clk after 5 ns;

    dut: entity work.pwm
        generic map (PERIOD => PERIOD, WIDTH => 4)
        port map (clk => clk, rst_n => rst_n, duty => duty, pwm_out => pwm_out, period_start => period_start);

    process
        type int_array is array (natural range <>) of natural;
        constant duties : int_array := (3, 0, 8, 15, 5, 1, 7);
        variable cnt, dq, new_duty, errors : natural := 0;
        variable exp_pwm, exp_start : std_logic;
    begin
        wait until falling_edge(clk);
        wait until falling_edge(clk);
        rst_n <= '1';
        for i in 0 to 16 * PERIOD - 1 loop
            -- Change duty every 11 cycles, deliberately out of step with the period.
            if i mod 11 = 0 then
                new_duty := duties((i / 11) mod duties'length);
                duty <= to_unsigned(new_duty, 4);
            end if;
            if cnt < dq then exp_pwm := '1'; else exp_pwm := '0'; end if;
            if cnt = 0 then exp_start := '1'; else exp_start := '0'; end if;
            if pwm_out /= exp_pwm or period_start /= exp_start then
                report "ERROR: cycle " & integer'image(i) & ": expected pwm/start " & std_logic'image(exp_pwm) &
                       std_logic'image(exp_start) & " got " & std_logic'image(pwm_out) & std_logic'image(period_start)
                       severity note;
                errors := errors + 1;
            end if;
            if cnt = PERIOD - 1 then
                cnt := 0;
                dq  := new_duty;
            else
                cnt := cnt + 1;
            end if;
            wait until falling_edge(clk);
        end loop;
        if errors = 0 then
            report "TEST PASSED" severity note;
        else
            report "TEST FAILED: " & integer'image(errors) & " errors" severity note;
        end if;
        std.env.finish;
    end process;
end architecture;
