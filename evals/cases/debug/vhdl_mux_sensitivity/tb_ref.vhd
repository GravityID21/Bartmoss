library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_ref is
end entity;

architecture sim of tb_ref is
    signal sel        : std_logic_vector(1 downto 0) := "11";
    signal a, b, c, d : std_logic_vector(7 downto 0) := x"00";
    signal y          : std_logic_vector(7 downto 0);
begin
    dut: entity work.mux4 port map (sel => sel, a => a, b => b, c => c, d => d, y => y);

    process
        variable errors : natural := 0;
        procedure check(expected : std_logic_vector(7 downto 0); what : string) is
        begin
            if y /= expected then
                report "ERROR: " & what & ": expected " & to_hstring(expected) & " got " & to_hstring(y) severity note;
                errors := errors + 1;
            end if;
        end procedure;
    begin
        a <= x"11"; b <= x"22"; c <= x"33"; d <= x"44";
        wait for 1 ns;
        for s in 0 to 3 loop
            sel <= std_logic_vector(to_unsigned(s, 2));
            wait for 1 ns;
            check(std_logic_vector(to_unsigned(16#11# * (s + 1), 8)), "select " & integer'image(s));
            -- Change the selected input while sel stays put: y must follow it.
            case s is
                when 0 => a <= x"A0";
                when 1 => b <= x"B1";
                when 2 => c <= x"C2";
                when others => d <= x"D3";
            end case;
            wait for 1 ns;
            case s is
                when 0 => check(x"A0", "input change on a");
                when 1 => check(x"B1", "input change on b");
                when 2 => check(x"C2", "input change on c");
                when others => check(x"D3", "input change on d");
            end case;
        end loop;
        if errors = 0 then
            report "TEST PASSED" severity note;
        else
            report "TEST FAILED: " & integer'image(errors) & " errors" severity note;
        end if;
        std.env.finish;
    end process;
end architecture;
