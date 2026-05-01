// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {SettlementRouter} from "./SettlementRouter.sol";
import {UserIntent} from "./IntentVault.sol";

/// @title ProtocolTreasury
/// @notice Protocol treasury and KeeperHub entry point.
///         Holds protocol revenue and gates keeper access to the SettlementRouter.
contract ProtocolTreasury is Ownable {
    using SafeERC20 for IERC20;

    SettlementRouter public settlementRouter;
    
    mapping(address => bool) public isKeeper;

    event KeeperAdded(address indexed keeper);
    event KeeperRemoved(address indexed keeper);
    event RouterSet(address indexed router);
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);
    event IntentForwarded(address indexed keeper, address indexed user, uint8 actionType, uint256 executeAmountIn);
    event TreasurySwapExecuted(address indexed tokenOut, address indexed to, uint256 amount);
    event ETHWithdrawn(address indexed to, uint256 amount);

    constructor(address _router) Ownable(msg.sender) {
        settlementRouter = SettlementRouter(_router);
        isKeeper[msg.sender] = true;
        emit KeeperAdded(msg.sender);
    }

    modifier onlyKeeper() {
        require(isKeeper[msg.sender], "Unauthorized: not a keeper");
        _;
    }

    // ── Owner Management ──

    function setSettlementRouter(address _router) external onlyOwner {
        require(_router != address(0), "Invalid router address");
        settlementRouter = SettlementRouter(_router);
        emit RouterSet(_router);
    }

    function addKeeper(address keeper) external onlyOwner {
        require(keeper != address(0), "Invalid keeper address");
        isKeeper[keeper] = true;
        emit KeeperAdded(keeper);
    }

    function removeKeeper(address keeper) external onlyOwner {
        isKeeper[keeper] = false;
        emit KeeperRemoved(keeper);
    }

    /// @notice Owner withdraws ERC20 (e.g. accumulated fees).
    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        require(to != address(0), "Invalid recipient");
        require(amount > 0, "Amount must be > 0");
        IERC20(token).safeTransfer(to, amount);
        emit TokenWithdrawn(token, to, amount);
    }

    /// @notice Owner withdraws ETH.
    function withdrawETH(address to, uint256 amount) external onlyOwner {
        require(to != address(0), "Invalid recipient");
        require(amount > 0, "Amount must be > 0");
        (bool success, ) = to.call{value: amount}("");
        require(success, "ETH transfer failed");
        emit ETHWithdrawn(to, amount);
    }

    /// @notice Keeper forwards an intent execution to the SettlementRouter.
    function forwardExecuteIntent(
        UserIntent calldata intent,
        bytes memory signature,
        uint8 actionType,
        bytes calldata executionData,
        uint256 executeAmountIn
    ) external onlyKeeper {
        emit IntentForwarded(msg.sender, intent.user, actionType, executeAmountIn);
        settlementRouter.executeIntent(intent, signature, actionType, executionData, executeAmountIn);
    }

    /// @notice Router-only: disburse treasury tokens to a user (internalization flow).
    function treasurySwap(address tokenOut, address to, uint256 amount) external {
        require(msg.sender == address(settlementRouter), "Unauthorized: only router");
        require(to != address(0), "Invalid recipient");
        require(amount > 0, "Amount must be > 0");
        IERC20(tokenOut).safeTransfer(to, amount);
        emit TreasurySwapExecuted(tokenOut, to, amount);
    }

    receive() external payable {}
}
